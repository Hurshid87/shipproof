# My agent said "deployed" 13 times. It was lying 13 times.

I build a customs classification engine. Small team — me. Deploys go out
several times a week, and for a while every one of them ended the same way:
the agent ran the deploy, the command exited 0, the agent wrote **"deployed
successfully"**, and I moved on.

Then a user would message me. The site was showing the old version. Or an
error page. Or it had been down for a minute and they gave up.

This happened thirteen times before I stopped blaming luck and looked at what
"deployed successfully" actually means.

## It means the command exited 0. That's all it means.

Here is the gap. Between "my deploy command finished" and "my users are being
served the new build" there are at least three things that can go wrong, and
none of them produce an error the agent can see:

**The deploy never ran.** Wrong branch, cached build, a platform that silently
skipped it. Your health check returns 200 — served by the *old* build, which
is perfectly healthy. Nothing looks wrong.

**The new build started and died.** It booted, passed the platform's health
check, crashed on the first real request, and the platform restored the
previous version. Total elapsed time: 40 seconds. Your agent finished
reporting success at second 12.

**It flapped.** New build, old build, new build, old build, while the
orchestrator makes up its mind. Whichever one you happened to look at is the
one you believed.

An agent cannot detect any of these, because its entire universe is the exit
code of a subprocess. It is not lying on purpose. It genuinely does not know.

## What I wanted was embarrassingly simple

I wanted the machine to answer one question: **is the thing I just deployed
the thing my users are getting right now?**

Not "is the server up" — an uptime monitor already answers that, and answers
it *yes*, cheerfully, while serving code from last Tuesday. A different
question.

So I wrote about forty lines that took a fingerprint of my site before the
deploy and waited for it to change afterward. It caught the next silent
rollback the same week. I cleaned it up and it is now a tool called
`shipproof`.

```
$ shipproof snapshot https://myapp.com --marker-json version
  current build: 2.0

$ ./deploy.sh

$ shipproof verify https://myapp.com
  [    1s] DOWN — HTTP 502
  [    5s] UP again — outage lasted 4s
  [    5s] NEW BUILD live (3.0)

PROVED — new build live after 5s; held for the settle window; 1 outage, 4s
```

And when the deploy did not actually happen:

```
NOT PROVED — the response never changed within 900s —
             the old build is still serving (identity '2.0')
$ echo $?
1
```

That exit code is the whole point. It goes in CI. It goes in the agent's
instructions. "Done" now has to survive a check the agent does not control.

## The part I got wrong the first time

My first version had a bug that taught me more than the tool did.

When a deploy rolled back — new build appeared, old build came back — it
reported: *"the response never changed."*

Which is technically true at the moment you look, and completely misleading.
"Never changed" makes you think the deploy did not run. In reality it ran,
went live, and **died**. Those two failures need completely different
responses from you, and my tool was collapsing them into one message.

A test caught it. Now it says what happened:

```
NOT PROVED — the new build appeared but rolled back to the old one (1 flap);
             within 900s it never held for 30s
```

I think this is the actual lesson from the whole exercise. A verification
tool that reports the wrong reason is worse than no tool, because you now
trust a wrong answer instead of distrusting no answer.

## It refuses to work when it cannot prove anything

Here is the design decision I care about most, and the one people push back on.

If your page has a timestamp on it, or a CSRF nonce, or a rotating node id,
then its fingerprint changes between two requests **with no deploy at all**.
Compare before and after and you will always see a change — after a real
deploy, and after no deploy. The comparison proves nothing.

Most tools would shrug and report success. This one refuses:

```
$ shipproof snapshot https://myapp.com
shipproof: cannot verify — the marker changes on its own between two requests
with no deploy in between (saw: b555314…, d1f148c…, d238…). Point --url at a
stable endpoint, or pick an exact marker with --marker-json / --marker-header
$ echo $?
2
```

Exit code `2` is **cannot verify**, and it is deliberately not `1`
**not proved**. A green check you cannot trust is worse than a red one,
because you stop looking.

This comes straight from the other thing I build. My classifier assigns
customs codes, and a wrong code is a fine measured in thousands of dollars.
Its central rule is that it may not output an answer unless it can quote the
exact line of the official tariff that proves it — character for character.
No quote, no answer. Not "probably 8424", not a confidence score. Nothing.

Same rule here. **An answer without evidence is not an answer.**

## How to pick a marker

Cheapest thing your app already exposes:

```bash
# a version field in a JSON health endpoint  (best)
shipproof snapshot https://myapp.com/health --marker-json version

# a response header
shipproof snapshot https://myapp.com --marker-header x-version

# a build id embedded in HTML
shipproof snapshot https://myapp.com --marker-regex 'build="([^"]+)"'

# nothing to point at: hash the body (+ any version header the host sends)
shipproof snapshot https://myapp.com
```

If you have nothing, this is the smallest change that makes it exact:

```python
@app.get("/health")
def health():
    return {"status": "ok", "version": os.environ.get("GIT_SHA", "dev")}
```

One warning from experience: do not point it at an uptime counter. I tried
`up_s` on my own service and the tool refused — that number goes up on its
own every second, so it would "change" whether or not I deployed. It caught
my mistake before I shipped it, which is roughly the highest praise I can
give a thing I wrote myself.

Likewise, if your marker returns `"ok"`, it warns you: that is a fixed status,
not a build identity, and it will never change across a deploy.

## Give it to the agent

Put this in `CLAUDE.md` / `AGENTS.md` / `.cursorrules`:

```markdown
## Deploys
Never report a deploy as done because a command exited 0.
1. Before deploying: `shipproof snapshot <url> --marker-json version`
2. Deploy.
3. `shipproof verify <url>` — non-zero means the deploy did NOT land.
   Report the failure verbatim. Do not say "deployed".
```

The agent still cannot see whether the deploy landed. It just can no longer
claim that it did.

## Install

```bash
pip install shipproof
```

Python 3.9+, **zero dependencies**, MIT. Source and issues:
https://github.com/Hurshid87/shipproof

---

If you have your own story of an agent confidently reporting a deploy that
never happened, I would genuinely like to hear it — mostly because I want to
know which failure modes I still have not thought of.
