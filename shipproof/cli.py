"""Command line interface for shipproof."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .core import Fingerprint, Marker, ProofError, fingerprint, verify

DEFAULT_STATE = ".shipproof.json"

EPILOG = """\
typical use, around whatever command your agent runs to deploy:

  shipproof snapshot https://myapp.com
  ./deploy.sh                       # or: let the agent do its thing
  shipproof verify https://myapp.com

exit codes:
  0  proved: the new build is live and stayed live
  1  not proved: still the old build, never came back, or rolled back
  2  cannot verify: bad arguments, or the target has no stable marker
"""


def _add_marker_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("marker (how to tell one build from another)")
    g.add_argument("--marker-json", metavar="PATH", default="",
                   help="dotted path into a JSON response, e.g. version or build.sha")
    g.add_argument("--marker-header", metavar="NAME", default="",
                   help="response header carrying the build id, e.g. x-version")
    g.add_argument("--marker-regex", metavar="RE", default="",
                   help="regex against the body; group 1 is used when present")


def _marker_from(args: argparse.Namespace) -> Marker:
    return Marker.parse(args.marker_json, args.marker_header, args.marker_regex)


def _state_path(args: argparse.Namespace) -> Path:
    return Path(args.state or os.environ.get("SHIPPROOF_STATE") or DEFAULT_STATE)


def cmd_snapshot(args: argparse.Namespace) -> int:
    marker = _marker_from(args)
    print(f"shipproof: fingerprinting {args.url}")
    print(f"  marker: {marker.describe()}")
    fp = fingerprint(args.url, marker, samples=args.samples, gap=args.gap,
                     timeout=args.http_timeout,
                     on_warning=lambda w: print(f"  WARNING: {w}"))
    path = _state_path(args)
    path.write_text(fp.to_json(), encoding="utf-8")
    print(f"  current build: {fp.identity}")
    print(f"  saved to {path}")
    print("\nnow deploy. then run: shipproof verify " + args.url)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = _state_path(args)
    if not path.exists():
        raise ProofError(
            f"no snapshot at {path} — run 'shipproof snapshot {args.url}' BEFORE "
            f"deploying. Without a before-picture there is nothing to compare to.")
    before = Fingerprint.from_json(path.read_text(encoding="utf-8"))
    if before.url != args.url:
        raise ProofError(f"snapshot was taken for {before.url}, not {args.url}")

    age = time.time() - before.taken_at
    print(f"shipproof: verifying {args.url}")
    print(f"  marker: {before.marker().describe()}")
    print(f"  build before deploy: {before.identity} (snapshot {age/60:.0f} min old)")
    print(f"  waiting up to {args.timeout:.0f}s, must then hold {args.settle:.0f}s\n")

    res = verify(args.url, before, timeout_s=args.timeout, interval=args.interval,
                 settle_for=args.settle, http_timeout=args.http_timeout,
                 on_event=lambda m: print("  " + m, flush=True))

    print("\n" + res.summary())
    if args.json:
        print(json.dumps({
            "proved": res.proved, "reason": res.reason,
            "changed_at_s": res.changed_at, "settled_at_s": res.settled_at,
            "downtime_s": round(res.downtime_s, 1), "outages": len(res.outages),
            "longest_outage_s": round(max(res.outages), 1) if res.outages else 0,
            "flaps": res.flaps, "probes": res.probes,
            "old_identity": res.old_identity, "new_identity": res.new_identity,
        }, indent=2))
    if res.proved and args.keep is False:
        path.unlink(missing_ok=True)
    return 0 if res.proved else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="shipproof",
        description="Prove a deploy actually landed. Do not take the agent's word for it.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="shipproof 0.1.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("url", help="the URL your users actually hit")
    common.add_argument("--state", default="", metavar="FILE",
                        help=f"where to keep the snapshot (default {DEFAULT_STATE})")
    common.add_argument("--http-timeout", type=float, default=10.0, metavar="SEC",
                        help="per-request timeout (default 10)")

    s = sub.add_parser("snapshot", parents=[common],
                       help="record the current build BEFORE deploying")
    _add_marker_args(s)
    s.add_argument("--samples", type=int, default=3, metavar="N",
                   help="probes used to confirm the marker is stable (default 3)")
    s.add_argument("--gap", type=float, default=1.0, metavar="SEC",
                   help="pause between those probes (default 1)")
    s.set_defaults(func=cmd_snapshot)

    v = sub.add_parser("verify", parents=[common],
                       help="wait until the new build is proved live and stable")
    v.add_argument("--timeout", type=float, default=900.0, metavar="SEC",
                   help="give up after this long (default 900)")
    v.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                   help="seconds between probes (default 5)")
    v.add_argument("--settle", type=float, default=30.0, metavar="SEC",
                   help="how long the new build must hold (default 30)")
    v.add_argument("--json", action="store_true", help="also print a JSON report")
    v.add_argument("--keep", action="store_true",
                   help="keep the snapshot file after a successful verify")
    v.set_defaults(func=cmd_verify, keep=False)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProofError as e:
        print(f"shipproof: cannot verify — {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nshipproof: interrupted — nothing proved", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
