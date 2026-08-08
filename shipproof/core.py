"""Prove a deploy actually landed — don't take the agent's word for it.

The problem: AI coding agents report "deployed" the moment a command exits 0.
The old version can still be serving. The new one can start and crash back.
The rollout can flap. Nothing in the agent's transcript can tell you.

shipproof takes a fingerprint of the live URL *before* the deploy, then watches
until it can *prove* three things happened, in order:

  1. the response actually changed (the new build is really serving),
  2. the service came back if it went down (and how long that took),
  3. it stayed changed and healthy for a settle period (no crash-back).

If it cannot prove that, it exits non-zero and says exactly what it could not
prove. It never reports success on missing evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable

__all__ = [
    "Fingerprint",
    "Marker",
    "Probe",
    "Result",
    "fingerprint",
    "check_stability",
    "verify",
]

USER_AGENT = "shipproof/0.1 (+https://github.com/Hurshid87/shipproof)"

# Response headers that commonly carry a build identity. Checked in order;
# the first one present becomes part of the default fingerprint.
VERSION_HEADERS = (
    "x-version",
    "x-app-version",
    "x-build-id",
    "x-release",
    "x-commit",
    "x-git-sha",
    "x-render-instance-id",
    "x-vercel-id",
    "etag",
    "last-modified",
)


class ProofError(Exception):
    """Configuration is wrong, or evidence is impossible to collect."""


# --------------------------------------------------------------------------
# markers: what counts as "the response changed"
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Marker:
    """How to extract the identity of the running build from a response.

    Exactly one extraction mode is active. ``json_path`` and ``header`` are
    exact and cheap and should be preferred; ``regex`` is for HTML pages that
    embed a build id; ``body`` (the default) hashes the whole body and only
    works on URLs whose output is stable between deploys.
    """

    kind: str = "body"          # body | json | header | regex
    value: str = ""             # dotted path / header name / pattern

    @classmethod
    def parse(cls, json_path: str = "", header: str = "",
              regex: str = "") -> "Marker":
        given = [bool(json_path), bool(header), bool(regex)]
        if sum(given) > 1:
            raise ProofError(
                "choose one marker: --marker-json, --marker-header or --marker-regex")
        if json_path:
            return cls("json", json_path)
        if header:
            return cls("header", header.lower())
        if regex:
            try:
                re.compile(regex)
            except re.error as e:
                raise ProofError(f"--marker-regex is not a valid regex: {e}") from e
            return cls("regex", regex)
        return cls("body", "")

    def extract(self, status: int, headers: dict[str, str], body: bytes) -> str:
        """Return the build identity, or "" when this response carries none."""
        if self.kind == "header":
            return headers.get(self.value, "")

        if self.kind == "json":
            try:
                doc = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                return ""
            cur = doc
            for part in self.value.split("."):
                if isinstance(cur, list):
                    try:
                        cur = cur[int(part)]
                        continue
                    except (ValueError, IndexError):
                        return ""
                if not isinstance(cur, dict) or part not in cur:
                    return ""
                cur = cur[part]
            return "" if cur is None else str(cur)

        if self.kind == "regex":
            m = re.search(self.value, body.decode("utf-8", "replace"))
            if not m:
                return ""
            return m.group(1) if m.groups() else m.group(0)

        # body: hash of the payload, plus any version header the server sends.
        # The header is included because many hosts serve byte-identical HTML
        # across builds and only the header moves.
        h = hashlib.sha256(body).hexdigest()[:16]
        for name in VERSION_HEADERS:
            if headers.get(name):
                return f"{h}:{name}={headers[name]}"
        return h

    def describe(self) -> str:
        return {
            "body": "sha256 of the response body (+ version header if present)",
            "json": f"JSON field '{self.value}'",
            "header": f"response header '{self.value}'",
            "regex": f"regex {self.value!r} against the body",
        }[self.kind]


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------

@dataclass
class Probe:
    """One observation of the target URL."""

    up: bool
    status: int = 0
    identity: str = ""
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class Fingerprint:
    """The build identity observed before the deploy."""

    url: str
    identity: str
    status: int
    marker_kind: str
    marker_value: str
    taken_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Fingerprint":
        try:
            d = json.loads(raw)
            return cls(**{k: d[k] for k in
                          ("url", "identity", "status", "marker_kind",
                           "marker_value", "taken_at")})
        except (ValueError, KeyError, TypeError) as e:
            raise ProofError(f"snapshot file is corrupt or from another version: {e}") from e

    def marker(self) -> Marker:
        return Marker(self.marker_kind, self.marker_value)


def _fetch(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            headers = {k.lower(): v for k, v in r.headers.items()}
            return int(r.status), headers, body
    except urllib.error.HTTPError as e:
        # 4xx/5xx still carry evidence — a 502 is exactly what we want to time.
        body = b""
        try:
            body = e.read()
        except Exception:  # noqa: BLE001 - body is a bonus, status is the signal
            pass
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return int(e.code), headers, body


def probe(url: str, marker: Marker, timeout: float = 10.0,
          healthy: Callable[[int], bool] = lambda s: 200 <= s < 400) -> Probe:
    """Observe the URL once. Never raises on network trouble."""
    t0 = time.monotonic()
    try:
        status, headers, body = _fetch(url, timeout)
    except Exception as e:  # noqa: BLE001 - any transport failure means "down"
        return Probe(up=False, error=f"{type(e).__name__}: {e}",
                     elapsed_ms=int((time.monotonic() - t0) * 1000))
    ms = int((time.monotonic() - t0) * 1000)
    if not healthy(status):
        return Probe(up=False, status=status, error=f"HTTP {status}", elapsed_ms=ms)
    return Probe(up=True, status=status,
                 identity=marker.extract(status, headers, body), elapsed_ms=ms)


# --------------------------------------------------------------------------
# stability: refuse to work with a fingerprint that moves on its own
# --------------------------------------------------------------------------

def check_stability(url: str, marker: Marker, samples: int = 3,
                    gap: float = 1.0, timeout: float = 10.0,
                    sleep: Callable[[float], None] = time.sleep) -> list[Probe]:
    """Probe repeatedly with no deploy in between.

    If the identity changes here, it changes for reasons that have nothing to
    do with deploying — a timestamp on the page, a random nonce, a rotating
    node id. Verifying against such a target would report success for any
    deploy and for no deploy alike, so we refuse it instead of guessing.
    """
    probes: list[Probe] = []
    for i in range(max(1, samples)):
        if i:
            sleep(gap)
        probes.append(probe(url, marker, timeout))
    return probes


def _stability_error(probes: Iterable[Probe], marker: Marker) -> str | None:
    live = [p for p in probes if p.up]
    if not live:
        first = next(iter(probes), None)
        return f"target is not reachable ({first.error if first else 'no probes'})"
    ids = {p.identity for p in live}
    if "" in ids:
        return (f"marker found nothing in the response — {marker.describe()} "
                f"is not present on this URL")
    if len(ids) > 1:
        sample = ", ".join(sorted(ids)[:3])
        return ("the marker changes on its own between two requests with no deploy "
                f"in between (saw: {sample}). Point --url at a stable endpoint, or "
                "pick an exact marker with --marker-json / --marker-header")
    return None


# Values that are stable for the right reason (they never change) and useless
# for the wrong one: they will not change across a deploy either, so verify
# would always report "never changed". Worth a warning, not a refusal — a
# project really can ship a build id that happens to read "ok".
CONSTANT_LOOKING = frozenset({
    "ok", "true", "false", "up", "healthy", "alive", "running", "1", "0",
    "success", "online", "pass", "passed", "yes", "good", "active",
})


def constant_warning(identity: str, marker: Marker) -> str | None:
    """Warn when the chosen marker looks like a fixed status, not a build id."""
    if marker.kind == "body":
        return None
    if identity.strip().lower() in CONSTANT_LOOKING:
        return (f"the marker returned {identity!r}, which looks like a fixed status "
                f"rather than a build identity. If it never changes, verify will "
                f"always say the deploy did not land. Point the marker at a version, "
                f"commit sha or build id instead.")
    return None


def fingerprint(url: str, marker: Marker | None = None, samples: int = 3,
                gap: float = 1.0, timeout: float = 10.0,
                sleep: Callable[[float], None] = time.sleep,
                on_warning: Callable[[str], None] | None = None) -> Fingerprint:
    """Take the pre-deploy fingerprint, refusing unstable targets."""
    marker = marker or Marker()
    probes = check_stability(url, marker, samples, gap, timeout, sleep)
    err = _stability_error(probes, marker)
    if err:
        raise ProofError(err)
    good = next(p for p in probes if p.up)
    warn = constant_warning(good.identity, marker)
    if warn and on_warning:
        on_warning(warn)
    return Fingerprint(url=url, identity=good.identity, status=good.status,
                       marker_kind=marker.kind, marker_value=marker.value,
                       taken_at=time.time())


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

@dataclass
class Result:
    """What we were able to prove, and what we were not."""

    proved: bool
    reason: str = ""
    changed_at: float | None = None     # seconds from start until new identity
    settled_at: float | None = None     # seconds until it held for settle_for
    downtime_s: float = 0.0             # total seconds unreachable
    outages: list[float] = field(default_factory=list)
    flaps: int = 0                      # times it went back to the old identity
    old_identity: str = ""
    new_identity: str = ""
    probes: int = 0

    def summary(self) -> str:
        if not self.proved:
            return f"NOT PROVED — {self.reason}"
        parts = [f"new build live after {self.changed_at:.0f}s",
                 f"held for the settle window (at {self.settled_at:.0f}s)"]
        if self.outages:
            parts.append(f"{len(self.outages)} outage(s), {self.downtime_s:.0f}s total, "
                         f"longest {max(self.outages):.0f}s")
        else:
            parts.append("zero downtime")
        if self.flaps:
            parts.append(f"{self.flaps} rollback flap(s) seen before it settled")
        return "PROVED — " + "; ".join(parts)


def verify(url: str, before: Fingerprint, timeout_s: float = 900.0,
           interval: float = 5.0, settle_for: float = 30.0,
           http_timeout: float = 10.0,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic,
           on_event: Callable[[str], None] | None = None) -> Result:
    """Watch until the new build is proved live and stable, or time runs out.

    Proof requires, in this order: an identity different from ``before``,
    then ``settle_for`` seconds during which it stays that same new identity
    and stays reachable. A return to the old identity resets the settle timer
    and counts as a flap — that is a rollback, not a successful deploy.
    """
    marker = before.marker()
    res = Result(proved=False, old_identity=before.identity)

    t0 = clock()
    down_from: float | None = None
    change_at: float | None = None
    hold_from: float | None = None
    current_new = ""

    while True:
        now = clock() - t0
        if now >= timeout_s:
            break

        p = probe(url, marker, http_timeout)
        res.probes += 1
        now = clock() - t0

        if not p.up:
            if down_from is None:
                down_from = now
                if on_event:
                    on_event(f"[{now:5.0f}s] DOWN — {p.error}")
            hold_from = None            # unreachable breaks the settle window
            sleep(interval)
            continue

        if down_from is not None:
            outage = now - down_from
            res.outages.append(outage)
            res.downtime_s += outage
            if on_event:
                on_event(f"[{now:5.0f}s] UP again — outage lasted {outage:.0f}s")
            down_from = None

        if not p.identity:
            # The marker vanished mid-deploy (error page, partial rollout).
            # No identity means no evidence; wait, do not assume.
            hold_from = None
            if on_event:
                on_event(f"[{now:5.0f}s] marker missing in response — waiting")
            sleep(interval)
            continue

        if p.identity == before.identity:
            if change_at is not None:
                res.flaps += 1
                if on_event:
                    on_event(f"[{now:5.0f}s] ROLLED BACK to the old build — "
                             f"settle timer reset")
                change_at = None
                current_new = ""
            hold_from = None
            sleep(interval)
            continue

        # a different identity: this is the new build
        if change_at is None or p.identity != current_new:
            change_at = now
            current_new = p.identity
            hold_from = now
            if on_event:
                on_event(f"[{now:5.0f}s] NEW BUILD live ({p.identity})")
        elif hold_from is None:
            hold_from = now

        if hold_from is not None and now - hold_from >= settle_for:
            res.proved = True
            res.changed_at = change_at
            res.settled_at = now
            res.new_identity = current_new
            return res

        sleep(interval)

    # timed out — say precisely which piece of evidence is missing
    if down_from is not None:
        res.reason = (f"target has been unreachable for {clock() - t0 - down_from:.0f}s "
                      f"and never came back within {timeout_s:.0f}s")
    elif res.flaps:
        # A new build did appear at some point and then the old one came back.
        # Reporting "never changed" here would hide a rollback, which is the
        # single most misleading thing this tool could say.
        res.reason = (f"the new build appeared but rolled back to the old one "
                      f"({res.flaps} flap(s)); within {timeout_s:.0f}s it never held "
                      f"for {settle_for:.0f}s")
    elif change_at is None:
        res.reason = (f"the response never changed within {timeout_s:.0f}s — the old "
                      f"build is still serving (identity {before.identity!r})")
    else:
        res.reason = (f"the new build appeared but never held for {settle_for:.0f}s "
                      f"within {timeout_s:.0f}s")
    res.new_identity = current_new
    return res
