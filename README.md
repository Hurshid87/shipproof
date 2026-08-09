# shipproof

[![PyPI](https://img.shields.io/pypi/v/shipproof)](https://pypi.org/project/shipproof/)
[![Python](https://img.shields.io/pypi/pyversions/shipproof)](https://pypi.org/project/shipproof/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Tests](https://github.com/Hurshid87/shipproof/actions/workflows/test.yml/badge.svg)](https://github.com/Hurshid87/shipproof/actions/workflows/test.yml)

**Your agent said "deployed". Prove it.**

```bash
pip install shipproof
```

AI coding agents report success the moment a command exits 0. That is not the
same as your users getting the new build. The old version can still be serving.
The new one can start and crash back. The rollout can flap. Nothing in the
agent's transcript tells you which of those happened.

`shipproof` takes a fingerprint of your live URL **before** the deploy, then
watches until it can prove the new build is really there — or tells you exactly
what it could not prove.

```
$ shipproof snapshot https://myapp.com --marker-json version
shipproof: fingerprinting https://myapp.com
  marker: JSON field 'version'
  current build: 2.0
  saved to .shipproof.json

$ ./deploy.sh          # or: let the agent do its thing

$ shipproof verify https://myapp.com
  [    1s] DOWN — HTTP 502
  [    5s] UP again — outage lasted 4s
  [    5s] NEW BUILD live (3.0)

PROVED — new build live after 5s; held for the settle window (at 8s); 1 outage(s), 4s total, longest 4s
```

And when the agent was wrong:

```
NOT PROVED — the response never changed within 900s — the old build is still serving (identity '2.0')
$ echo $?
1
```

## Install

```bash
pip install shipproof
```

Python 3.9+. **Zero dependencies** — standard library only, so it drops into any
CI image without pulling a tree of packages.

## Why not a normal uptime monitor

Uptime monitors answer *"is the server responding?"*. That question was already
answered — yes, by the **old** build. `shipproof` answers a different one:

> did the thing I just deployed actually replace what was running, and did it stay?

Three failures it catches that a 200 OK does not:

| What happened | What a monitor says | What shipproof says |
|---|---|---|
| Deploy silently never ran | ✅ 200 OK | ❌ the old build is still serving |
| New build started, crashed, old one restored | ✅ 200 OK | ❌ rolled back after 1 flap |
| Deploy worked but took the site down for 40s | ✅ 200 OK (after) | ✅ proved, **1 outage, 40s** |

## It refuses to guess

Most tools in this space will happily tell you "OK" on evidence they do not
have. This one will not. If the fingerprint of your URL changes on its own —
a timestamp on the page, a rotating node id, a CSRF nonce — then it would
change with a deploy *and without one*, so a comparison proves nothing:

```
$ shipproof snapshot https://myapp.com
shipproof: cannot verify — the marker changes on its own between two requests
with no deploy in between (saw: b555314…, d1f148c…, d238…). Point --url at a
stable endpoint, or pick an exact marker with --marker-json / --marker-header
$ echo $?
2
```

Exit code `2` means *cannot verify*, distinct from `1` *not proved*. A tool that
cannot tell those apart is a tool you cannot trust in CI.

## Markers: how it tells one build from another

Pick the cheapest one your app already exposes.

```bash
# a version field in a JSON health endpoint  (best)
shipproof snapshot https://myapp.com/health --marker-json version
shipproof snapshot https://myapp.com/health --marker-json build.sha

# a response header
shipproof snapshot https://myapp.com --marker-header x-version

# a build id embedded in HTML
shipproof snapshot https://myapp.com --marker-regex 'build="([^"]+)"'

# nothing to point at: hash the body (+ any version header the host sends)
shipproof snapshot https://myapp.com
```

If your app exposes nothing, the smallest possible change makes this exact:

```python
@app.get("/health")
def health():
    return {"status": "ok", "version": os.environ.get("GIT_SHA", "dev")}
```

## Use it in CI

```yaml
- name: fingerprint before deploy
  run: shipproof snapshot https://myapp.com --marker-json version

- name: deploy
  run: ./deploy.sh

- name: prove it landed
  run: shipproof verify https://myapp.com --timeout 900 --settle 60
```

The job fails on exit 1. Nobody has to read a log to find out the deploy lied.

## Give it to your agent

Put this in `CLAUDE.md`, `AGENTS.md`, `.cursorrules` — whatever your agent reads:

```markdown
## Deploys
Never report a deploy as done on the basis of a command exiting 0.
1. Before deploying: `shipproof snapshot <url> --marker-json version`
2. Deploy.
3. `shipproof verify <url>` — if it exits non-zero, the deploy did NOT land.
   Report the failure verbatim. Do not say "deployed".
```

Now "done" has to survive a check the agent does not control.

## Options

```
shipproof snapshot URL [--marker-json PATH | --marker-header NAME | --marker-regex RE]
                       [--samples N] [--gap SEC] [--state FILE] [--http-timeout SEC]

shipproof verify   URL [--timeout SEC]   # give up after this (default 900)
                       [--interval SEC]  # seconds between probes (default 5)
                       [--settle SEC]    # how long the new build must hold (default 30)
                       [--json]          # machine-readable report
                       [--keep]          # keep the snapshot after success
```

`--settle` is the one worth tuning. A container that boots, serves one request
and dies will pass a 5-second settle and fail a 60-second one. On a platform
with slow health checks, set it to 60 or more.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | **proved** — the new build is live and held for the settle window |
| `1` | **not proved** — old build still serving, never came back, or rolled back |
| `2` | **cannot verify** — bad arguments, no snapshot, or no stable marker |

## As a library

```python
from shipproof import Marker, fingerprint, verify

before = fingerprint("https://myapp.com/health", Marker.parse(json_path="version"))
# ... deploy ...
result = verify("https://myapp.com/health", before, timeout_s=900, settle_for=60)

if not result.proved:
    raise SystemExit(result.reason)
print(f"landed in {result.changed_at:.0f}s, downtime {result.downtime_s:.0f}s")
```

## Where this came from

Building a customs-classification engine, my production site went down silently
after a deploy thirteen times. Every time the tooling said the deploy succeeded.
The site was serving the old build, or an error page, and I found out from a
user. So I wrote the smallest thing that could tell me the truth, and this is
that thing, generalised.

The rule it enforces is the same one that engine runs on: **an answer without
evidence is not an answer.**

## License

MIT
