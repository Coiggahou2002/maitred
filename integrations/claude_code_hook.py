#!/usr/bin/env python3
"""Claude Code PreToolUse(Bash) hook: send heavy commands through `headroom run`.

Agents never have to know headroom exists: a test suite, a build or a type check is rewritten
to `headroom run --cpu N --mem XG --name … -- /bin/zsh -c '<command>'` before it runs, so it
waits its turn instead of piling onto a full machine. Everything else passes through untouched.

Install: see README ("Claude Code"). Escape hatch: put `# headroom-ok` in the command.
"""
import json
import os
import re
import shlex
import shutil
import sys

ESCAPE = "headroom-ok"
HEADROOM = shutil.which("headroom") or os.path.expanduser("~/.local/bin/headroom")

_RUN = r"(?:uv\s+run\s+(?:--\S+\s+)*|poetry\s+run\s+|python3?\s+-m\s+|npx\s+(?:--\S+\s+)*|bunx\s+|pnpm\s+exec\s+)?"
# (pattern at command position, cpu cores, memory prior in GB). headroom learns the real peak.
HEAVY = [(re.compile(p), cpu, mem) for p, cpu, mem in (
    (_RUN + r"pytest\b", 8, 3),
    (_RUN + r"(?:mypy|tsc|pyright)\b", 2, 2),
    (_RUN + r"(?:vitest|jest)\b", 4, 4),
    (_RUN + r"playwright\s+test\b", 4, 4),
    (r"(?:npm|pnpm|yarn|bun)\s+(?:--prefix[= ]\S+\s+)?(?:run\s+)?(?:test|e2e)\b", 4, 6),
    (r"(?:npm|pnpm|yarn|bun)\s+(?:--prefix[= ]\S+\s+)?(?:run\s+)?build\b", 4, 3),
    (r"docker(?:-compose|\s+compose)?\s+(?:\S+\s+)*?build\b", 4, 2),
    (r"xcodebuild\b", 8, 6),
    (r"swift\s+(?:build|test)\b", 8, 4),
    (r"cargo\s+(?:build|test|clippy)\b", 8, 4),
    (r"go\s+(?:build|test)\b", 8, 4),
    (r"(?:make|gradle|\./gradlew|mvn)\b", 8, 4),
)]
_SEP = re.compile(r";|&&|\|\||\||\n|&")
_LEAD = re.compile(r"^(?:(?:env|nohup|time|command)\s+|\w+=\S*\s+)+")
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\1\s*(?:\n|$)", re.S)


def segments(command):
    for part in _SEP.split(_HEREDOC.sub("\n", command)):
        seg = _LEAD.sub("", part.strip().lstrip("({ "))
        if seg:
            yield seg


def match(command):
    for seg in segments(command):
        if seg.startswith(("headroom ", HEADROOM)):
            return None
        for pat, cpu, mem in HEAVY:
            if pat.match(seg):
                return seg, cpu, mem
    return None


def main():
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command or ESCAPE in command or not os.path.exists(HEADROOM):
        return
    hit = match(command)
    if not hit:
        return
    seg, cpu, mem = hit
    words = [w for w in seg.split() if not w.startswith("-")][:4]
    name = " ".join(words) + " @" + os.path.basename(os.path.normpath(payload.get("cwd") or "."))
    wrapped = (f"{shlex.quote(HEADROOM)} run --cpu {cpu} --mem {mem}G --name {shlex.quote(name)}"
               f" -- /bin/zsh -c {shlex.quote(command)}")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "updatedInput": {**tool_input, "command": wrapped}}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a hook must never break the agent's command
