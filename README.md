# maitred

**Your Mac stops freezing when AI agents work on it.**

A laptop used to carry one person's worth of work. Now it carries ten coding agents, and each of
them can start a test suite, a build or a headless browser. Together they take every core and
every gigabyte. macOS starts killing apps ("Your system has run out of application memory —
Claude is using 64 GB"), and the only way out is a hard reboot.

maitred is the maître d' for that machine. Heavy work waits at the door until there is a table
free, and anything that grows wild gets shown out before it takes the room down. You install it
once. After that, the only thing you notice is that the Mac doesn't lock up.

```
git clone https://github.com/Coiggahou2002/maitred ~/maitred
~/maitred/maitred install --claude-code
```

That's the whole setup. macOS 12+, any `python3` ≥ 3.9 (the one Xcode's command line tools
ship is enough), no dependencies. What `install` does, so nothing is a surprise:

- links `~/.local/bin/maitred` to the clone — put `~/.local/bin` on your `PATH` if it isn't (install tells you);
- loads the guard as `~/Library/LaunchAgents/dev.maitred.guard.plist` (starts at login, restarted if it dies);
- with `--claude-code`, adds one `PreToolUse` hook to `~/.claude/settings.json` (backup next to it as
  `settings.json.bak-maitred`; `MAITRED_CLAUDE_SETTINGS=<path>` writes elsewhere). New Claude Code
  sessions pick it up; running ones need a restart.

All three point at the clone, so keep it where it is — after moving it, run `install` again.
`maitred uninstall` removes the guard, the link and the hook; it leaves your settings and history.

## What it does

**1. A queue for heavy work.** `maitred run -- <cmd>` starts a command only when the machine has
room for it. Jobs go in first-come, first-served order.

- **CPU.** Each job reserves cores (half the machine by default). A job that doesn't fit waits.
- **Memory.** Each job reserves memory. The reservation starts from a prior (`--mem`, default
  2 GB). maitred measures what that same command actually peaked at, and raises the next
  reservation to match. You never have to estimate anything.
- **Priority.** Jobs run at `nice +10`, so whatever you are clicking on stays fast.
- **Hands-free with Claude Code.** With `--claude-code`, every heavy shell command an agent runs
  goes through the queue automatically, with a starting reservation per kind of command (raised
  later to what it really used):

  | command | cores | memory prior |
  |---|---|---|
  | pytest, cargo, go, make, gradle, mvn, xcodebuild, swift | 8 | 3–6 GB |
  | npm / pnpm / yarn / bun `test`, `e2e` | 4 | 6 GB |
  | npm … `build`, vitest, jest, playwright test | 4 | 3–4 GB |
  | tsc, mypy, pyright | 2 | 2 GB |
  | docker build | 4 | 2 GB (the build itself runs in Docker's VM) |

  Everything else runs untouched. Put `# maitred-ok` in a command to skip the queue.

**2. A memory guard.** A LaunchAgent checks once a second. It measures memory the way macOS does:
`phys_footprint` per process (the figure Activity Monitor and the out-of-memory dialog show), and
free memory from `vm_stat` together with the kernel's `memorystatus_level`.

| when | it does |
|---|---|
| one process started by an agent app grows past 22 % of RAM | kills it and sends a notification. A test runner at 246 GB is never legitimate |
| one queued job's whole process tree passes 30 % of RAM | kills it, and the job says why |
| everything agents started passes its budget (40 % of RAM), or the Mac drops below 15 % free | pauses the newest job (`SIGSTOP`) and starts nothing new |
| work falls back under 85 % of budget and the Mac is above 25 % free | resumes the paused job |
| a job stays paused for 3 minutes and memory never comes back | kills it, so it stops holding memory |
| agent work passes 115 % of budget | kills enough of the largest agent tasks, queued or not, to get back under budget in one step |
| the Mac drops below 8 % free, or the kernel reports critical pressure | kills the largest things agents started, 1 GB and up |

"Agent apps" means Claude, Claude Code, Cursor, Codex, Windsurf, Paseo, opencode, gemini and
aider. maitred never pauses or kills the apps themselves, only what they spawned, so your
sessions survive.

When the guard kills a job, the job exits with code 137 and prints the reason on stderr. The agent
that ran it can read that reason and try again later. `maitred status` shows what is running,
paused and queued, and what the guard did recently.

## Measured

These numbers come from a 48 GB M-series Mac with the real guard and default settings
(`tests/storm.py`-style run). The load was six queued jobs that each needed 4 GB, plus four
processes outside the queue that each grew to 5 GB. That is about 44 GB of incompressible demand.

| | |
|---|---|
| peak memory used by agent work | 24.8 GB (budget 19.2 GB, enforcement starts at 22 GB) |
| lowest free memory on the Mac | 19 % |
| memory pressure | normal throughout |
| worst scheduling hiccup for a 100 ms timer | 10 ms |
| outcome | 4 queued jobs finished after waiting their turn; the guard stopped 2 queued jobs and 2 runaway processes |

A single 12 GB process started outside the queue was killed 1 s after it crossed the limit.

## Commands

```
maitred run [--mem 4G] [--cpu 8] [--name X] -- <cmd…>   queue a command
maitred status                                          memory, budget, jobs, recent actions
maitred report [--days 7]                               did it work? guard actions per day, and how often macOS ran out of memory or was force-restarted
maitred install [--claude-code]                         CLI + guard (+ Claude Code hook)
maitred uninstall                                       remove the guard
```

`--name` is the key maitred learns memory under; by default it is the command's first words plus
the directory, so `pytest tests/unit` and `pytest tests/` are learned separately. `maitred <cmd…>`
is shorthand for `maitred run -- <cmd…>` (except for the words `run`, `status`, `install`,
`uninstall`).

To check that the guard is alive, look for `guard running` in `maitred status`, or run
`launchctl list dev.maitred.guard`. Its log is `~/.local/state/maitred/events.log`.

Other agent tools can prefix heavy commands with `maitred run --`, or point their own command
hook at `integrations/claude_code_hook.py`. A `maitred run` inside a job doesn't queue a second
time.

## Configuration

Configuration is optional. Settings go in `~/.config/maitred/config.json`, which is re-read every
second, or in `MAITRED_<KEY>` environment variables:

```json
{
  "agent_budget_pct": 40,
  "runaway_pct": 22,
  "job_cap_pct": 30,
  "soft_pct": 15, "resume_pct": 25, "hard_pct": 8,
  "admit_floor_pct": 20,
  "default_cpu": 8, "default_mem_gb": 2,
  "pause_max_s": 180,
  "notify": true
}
```

## Limits

- **It is a user-space guard, not a kernel limit.** macOS has no cgroups. The guard looks once a
  second, so memory that grows faster than that overshoots by about one second's worth before it
  is stopped (2.8 GB in the run above).
- **Docker's VM is outside it.** Containers live inside one VM process whose size you set in
  Docker Desktop → Resources. If Docker is your biggest memory user, lower that setting.
- **Pausing does not free memory.** It stops growth and lets macOS compress the paused pages. That
  is why a pause that doesn't help turns into a kill after `pause_max_s`.
- **Only `maitred run` jobs wait in the queue.** Everything else is covered only by the runaway,
  budget and emergency rules.
- **A kill is a kill.** The guard can end a test run. That is the trade: one failed run with a
  clear reason, instead of a frozen machine.

## Related projects

- [aha-churchdesk/headroom](https://github.com/aha-churchdesk/headroom) is a Rust menu-bar app
  with a memory-aware queue for agent commands. It is more polished than maitred's queue, and its
  guard acts only on the jobs it queued.
- [Kevin-Liu-01/headroom](https://github.com/Kevin-Liu-01/headroom) monitors which agent session
  owns the memory and alerts you. It doesn't enforce anything.

What maitred adds is enforcement over everything agents start, queued or not: a runaway limit, a
total budget for agent work, and a one-step return under that budget. It is also one stdlib
Python file that you can read in ten minutes.

## Tests

```
python3 -m unittest -v tests/test_maitred.py   # from the repo root, ~1 minute, allocates up to ~4 GB
python3 tests/storm.py                         # optional load test against the installed guard, ~2 minutes, ~44 GB of demand
```

The tests run real processes that allocate real memory, against a real guard. Each test is scoped
to its own process tree, so nothing else on the machine is touched.

MIT licensed.
