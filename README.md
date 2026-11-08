# headroom

**Your Mac stops freezing when AI agents work on it.**

A laptop used to carry one person's worth of work. Now it carries ten coding agents, each of which
can start a test suite, a build, a headless browser. Together they take every core and every
gigabyte, macOS starts killing apps ("Your system has run out of application memory — Claude is
using 64 GB"), and the only way out is a hard reboot.

headroom is a queue for heavy work plus a guard for memory. Install it once; afterwards the only
thing you notice is that the machine doesn't lock up.

```
headroom install            # one time: CLI + background guard (a LaunchAgent)
headroom run -- npm test    # waits for room, then runs at low priority
headroom status             # what's running, paused, queued, and what the guard did
```

## What it does

**1. Queue (admission).** `headroom run -- <cmd>` starts the command only when the machine has
room for it, first come first served:

- CPU: each job reserves cores (default: half the machine). Jobs that don't fit wait.
- Memory: each job reserves memory. The reservation starts from a prior (`--mem`, default 2 GB)
  and is raised to what that same command actually peaked at last time — headroom measures it,
  you never have to.
- It starts nothing new while the Mac is short on memory or a job is paused.
- The job runs at `nice +10`, so whatever you are clicking on stays fast.

**2. Guard (enforcement).** A LaunchAgent checks once a second, using the same numbers macOS uses
(per-process `phys_footprint`, which is what Activity Monitor and the "out of memory" dialog show,
and the kernel's `memorystatus_level`):

| situation | action |
|---|---|
| a single process an agent app started grows past 22 % of RAM | killed, with a notification — a test runner at 246 GB is never legitimate |
| a queued job's whole process tree passes 30 % of RAM | killed; the job prints why |
| agents together pass their budget (55 % of RAM) or the Mac drops below 20 % free | newest job paused (`SIGSTOP`) |
| memory comes back (agents < 85 % of budget and > 35 % free) | paused job resumed |
| a job stays paused 3 minutes without memory coming back | killed, so it stops holding memory |
| agents pass 115 % of budget | largest queued job killed |
| the Mac drops below 10 % free or the kernel reports critical pressure | largest thing any agent started (≥ 1 GB) killed |

"Agent apps" are Claude, Claude Code, Cursor, Codex, Windsurf, Paseo, opencode, gemini, aider.
The apps themselves are never paused or killed — only what they spawned. Every action is logged
and shown by `headroom status`; a killed job exits 137 and says why on stderr, so the agent that
ran it can read the reason and retry later.

## Install

macOS 12+, the system `python3`, nothing else.

```
git clone https://github.com/<you>/headroom ~/headroom
~/headroom/headroom install
```

`install` links the CLI into `~/.local/bin` and loads `~/Library/LaunchAgents/dev.headroom.guard.plist`
(starts at login, restarts if it dies). `headroom uninstall` removes the guard.

Check it is alive: `headroom status` (line `guard running`) or `launchctl list dev.headroom.guard`.

## Claude Code

Agents shouldn't have to remember a wrapper. Add the bundled hook and every heavy command
(pytest, npm test/build, vitest, playwright, tsc, mypy, docker build, xcodebuild, cargo, go, make …)
is routed through the queue automatically:

```jsonc
// ~/.claude/settings.json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "python3 ~/headroom/integrations/claude_code_hook.py" }] }
    ]
  }
}
```

Other agents: prefix heavy commands with `headroom run --`, or point their command hook at the
same script. Nested calls (a `headroom run` inside a job) don't queue twice.

## Configuration

Optional. `~/.config/headroom/config.json`, re-read every second, or `HEADROOM_<KEY>` env vars:

```json
{
  "agent_budget_pct": 55,
  "runaway_pct": 22,
  "job_cap_pct": 30,
  "soft_pct": 20, "resume_pct": 35, "hard_pct": 10,
  "admit_floor_pct": 25,
  "default_cpu": 8, "default_mem_gb": 2,
  "pause_max_s": 180,
  "notify": true,
  "hosts": ["/Claude\\.app/", "/Cursor\\.app/", "..."]
}
```

## Limits — read before trusting it

- **It is a user-space guard, not a kernel limit.** macOS has no cgroups. headroom polls once a
  second; a process that allocates faster than ~10 GB/s can briefly overshoot before it is killed.
- **Docker's VM is outside it.** Containers live in one VM process whose size you set in Docker
  Desktop → Resources. Lower that if Docker is your biggest consumer.
- **Pausing does not free memory**, it stops growth and lets macOS compress the paused pages. That
  is why a pause that doesn't help turns into a kill after `pause_max_s`.
- **Only jobs started through `headroom run` are queued.** Everything else is only protected by the
  runaway and emergency rules.
- **A kill is a kill.** The guard never touches the agent apps, but it will end a test run. That is
  the trade: one failed run with a clear reason instead of a frozen machine.

## Tests

```
python3 -m unittest -v tests/test_headroom.py
```

They run real processes that allocate real memory against a real guard, scoped to the test's own
process tree so nothing else on the machine is touched.

MIT licensed.
