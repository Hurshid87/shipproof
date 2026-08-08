"""Tests for shipproof.

The whole value of this tool is that it refuses to claim success without
evidence, so most of these tests are about the ways it must say "no".
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shipproof.core import (  # noqa: E402
    Fingerprint,
    constant_warning,
    Marker,
    Probe,
    ProofError,
    _stability_error,
    fingerprint,
    verify,
)


# --------------------------------------------------------------------------
# markers
# --------------------------------------------------------------------------

def test_body_marker_is_stable_for_identical_bodies():
    m = Marker()
    a = m.extract(200, {}, b"<html>hello</html>")
    b = m.extract(200, {}, b"<html>hello</html>")
    assert a == b and a


def test_body_marker_changes_when_body_changes():
    m = Marker()
    assert m.extract(200, {}, b"v1") != m.extract(200, {}, b"v2")


def test_body_marker_folds_in_version_header():
    m = Marker()
    same_body = b"<html>identical</html>"
    a = m.extract(200, {"x-version": "1.0"}, same_body)
    b = m.extract(200, {"x-version": "1.1"}, same_body)
    assert a != b, "byte-identical pages must still be told apart by the header"


def test_json_marker_reads_nested_path():
    m = Marker.parse(json_path="build.sha")
    body = json.dumps({"build": {"sha": "abc123"}}).encode()
    assert m.extract(200, {}, body) == "abc123"


def test_json_marker_walks_lists():
    m = Marker.parse(json_path="items.1.id")
    body = json.dumps({"items": [{"id": "a"}, {"id": "b"}]}).encode()
    assert m.extract(200, {}, body) == "b"


def test_json_marker_returns_empty_on_missing_field():
    m = Marker.parse(json_path="nope.here")
    assert m.extract(200, {}, json.dumps({"a": 1}).encode()) == ""


def test_json_marker_survives_non_json_body():
    m = Marker.parse(json_path="version")
    assert m.extract(200, {}, b"<html>not json</html>") == ""


def test_header_marker_is_case_insensitive():
    m = Marker.parse(header="X-Version")
    assert m.extract(200, {"x-version": "7"}, b"") == "7"


def test_regex_marker_uses_first_group():
    m = Marker.parse(regex=r'build="([^"]+)"')
    assert m.extract(200, {}, b'<meta build="2026.08.07">') == "2026.08.07"


def test_regex_marker_without_group_uses_whole_match():
    m = Marker.parse(regex=r"v\d+\.\d+")
    assert m.extract(200, {}, b"running v4.2 now") == "v4.2"


def test_two_markers_at_once_is_rejected():
    with pytest.raises(ProofError):
        Marker.parse(json_path="a", header="b")


def test_invalid_regex_is_rejected_early():
    with pytest.raises(ProofError):
        Marker.parse(regex="(unclosed")


# --------------------------------------------------------------------------
# stability gate — the honesty guarantee
# --------------------------------------------------------------------------

def test_stable_target_passes():
    probes = [Probe(up=True, status=200, identity="same") for _ in range(3)]
    assert _stability_error(probes, Marker()) is None


def test_target_that_changes_on_its_own_is_refused():
    probes = [Probe(up=True, status=200, identity=x) for x in ("a", "b", "a")]
    err = _stability_error(probes, Marker())
    assert err and "changes on its own" in err


def test_missing_marker_is_refused():
    probes = [Probe(up=True, status=200, identity="") for _ in range(3)]
    err = _stability_error(probes, Marker.parse(json_path="version"))
    assert err and "found nothing" in err


def test_unreachable_target_is_refused():
    probes = [Probe(up=False, error="timeout") for _ in range(3)]
    err = _stability_error(probes, Marker())
    assert err and "not reachable" in err


def test_fingerprint_raises_on_unstable_target(monkeypatch):
    seq = iter(["one", "two", "three"])
    monkeypatch.setattr("shipproof.core.probe",
                        lambda *a, **k: Probe(up=True, status=200, identity=next(seq)))
    with pytest.raises(ProofError):
        fingerprint("https://x.test", samples=3, gap=0, sleep=lambda s: None)


# --------------------------------------------------------------------------
# verify — a fake clock so the suite runs instantly
# --------------------------------------------------------------------------

class FakeWorld:
    """Replays a scripted sequence of probes against a virtual clock."""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.t = 0.0

    def probe(self, *_a, **_k):
        p = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return p

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t


def run(script, monkeypatch, old="OLD", **kw):
    world = FakeWorld(script)
    monkeypatch.setattr("shipproof.core.probe", world.probe)
    before = Fingerprint("https://x.test", old, 200, "body", "", 0.0)
    opts = dict(timeout_s=600, interval=5, settle_for=30, sleep=world.sleep,
                clock=world.clock)
    opts.update(kw)
    return verify("https://x.test", before, **opts)


UP_OLD = Probe(up=True, status=200, identity="OLD")
UP_NEW = Probe(up=True, status=200, identity="NEW")
DOWN = Probe(up=False, status=502, error="HTTP 502")


def test_proves_a_clean_deploy(monkeypatch):
    res = run([UP_OLD, UP_NEW] + [UP_NEW] * 20, monkeypatch)
    assert res.proved
    assert res.downtime_s == 0 and res.flaps == 0
    assert "PROVED" in res.summary() and "zero downtime" in res.summary()


def test_refuses_when_the_old_build_keeps_serving(monkeypatch):
    res = run([UP_OLD] * 200, monkeypatch)
    assert not res.proved
    assert "never changed" in res.reason
    assert "old build is still serving" in res.reason


def test_measures_the_outage_window(monkeypatch):
    res = run([UP_OLD, DOWN, DOWN, DOWN] + [UP_NEW] * 20, monkeypatch)
    assert res.proved
    assert res.downtime_s == pytest.approx(15.0, abs=6)
    assert len(res.outages) == 1


def test_catches_a_rollback_and_does_not_call_it_success(monkeypatch):
    # new build appears, then the old one comes back and stays: that is a
    # failed deploy, even though a "new version was seen" at some point.
    res = run([UP_OLD, UP_NEW, UP_NEW, UP_OLD] + [UP_OLD] * 200, monkeypatch)
    assert not res.proved
    assert res.flaps >= 1
    assert "rolled back" in res.reason, "a rollback must not be reported as 'never changed'"


def test_flap_then_success_is_proved_but_reported(monkeypatch):
    res = run([UP_OLD, UP_NEW, UP_OLD, UP_NEW] + [UP_NEW] * 20, monkeypatch)
    assert res.proved and res.flaps == 1
    assert "flap" in res.summary()


def test_settle_window_is_enforced(monkeypatch):
    # new build shows up but the target dies before the settle window ends
    script = [UP_OLD, UP_NEW, UP_NEW] + [DOWN] * 400
    res = run(script, monkeypatch, timeout_s=200)
    assert not res.proved
    assert "unreachable" in res.reason


def test_missing_marker_mid_deploy_does_not_count_as_change(monkeypatch):
    blank = Probe(up=True, status=200, identity="")
    res = run([UP_OLD, blank, blank] + [UP_OLD] * 200, monkeypatch)
    assert not res.proved
    assert "never changed" in res.reason


def test_never_reachable_reports_that_precisely(monkeypatch):
    res = run([DOWN] * 400, monkeypatch, timeout_s=100)
    assert not res.proved
    assert "unreachable" in res.reason and "never came back" in res.reason


def test_result_records_both_identities(monkeypatch):
    res = run([UP_OLD, UP_NEW] + [UP_NEW] * 20, monkeypatch)
    assert res.old_identity == "OLD" and res.new_identity == "NEW"


# --------------------------------------------------------------------------
# snapshot file round trip
# --------------------------------------------------------------------------

def test_fingerprint_json_round_trip():
    fp = Fingerprint("https://x.test", "abc", 200, "json", "version", 1234.5)
    again = Fingerprint.from_json(fp.to_json())
    assert again == fp
    assert again.marker() == Marker("json", "version")


def test_corrupt_snapshot_is_rejected():
    with pytest.raises(ProofError):
        Fingerprint.from_json("{not json")


def test_snapshot_missing_keys_is_rejected():
    with pytest.raises(ProofError):
        Fingerprint.from_json('{"url": "https://x.test"}')


# --------------------------------------------------------------------------
# constant-looking markers: warn, do not refuse
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["ok", "OK", " healthy ", "true", "up", "1"])
def test_constant_looking_marker_warns(value):
    w = constant_warning(value, Marker.parse(json_path="status"))
    assert w and "fixed status" in w


@pytest.mark.parametrize("value", ["2.1.0", "abc123f", "2026.08.07", "v4"])
def test_real_build_ids_do_not_warn(value):
    assert constant_warning(value, Marker.parse(json_path="version")) is None


def test_body_hash_never_warns():
    assert constant_warning("ok", Marker()) is None


def test_warning_reaches_the_caller(monkeypatch):
    monkeypatch.setattr("shipproof.core.probe",
                        lambda *a, **k: Probe(up=True, status=200, identity="ok"))
    seen = []
    fingerprint("https://x.test", Marker.parse(json_path="status"),
                samples=2, gap=0, sleep=lambda s: None, on_warning=seen.append)
    assert seen and "fixed status" in seen[0]


def test_warning_does_not_block_the_snapshot(monkeypatch):
    monkeypatch.setattr("shipproof.core.probe",
                        lambda *a, **k: Probe(up=True, status=200, identity="ok"))
    fp = fingerprint("https://x.test", Marker.parse(json_path="status"),
                     samples=2, gap=0, sleep=lambda s: None)
    assert fp.identity == "ok", "a warning must not stop the user"
