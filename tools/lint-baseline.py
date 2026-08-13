#!/usr/bin/env python3
"""Frozen-baseline linting for the launchers tree: fail on a NEW finding, ignore the old ones.

WHY A BASELINE AND NOT A CLEAN SLATE. This repo ran with no linter at all until audit 2026-08-12
phase 5. Turning one on found 197 ruff findings and 270 shellcheck findings. Most are harmless
(around 110 of the shellcheck ones are false positives from sourcing EmuDeck's own variables), a
few are real, and fixing all of them in one commit would be a huge unreviewable diff in code that
works. So today's counts are frozen here and CI's only job is to stop the number going UP.

WHY (file, code) COUNTS AND NOT LINE NUMBERS. Line numbers churn on every edit, which would make
the baseline useless within a day. Counting per file and per rule survives a refactor that moves
code around, while still catching a genuinely new occurrence in a file that already had some -
which a plain "file has this code somewhere" baseline would miss.

DIRECTION MATTERS. A count going UP fails: that is a new finding, look at it. A count going DOWN
only warns: an unrelated commit that happens to clean something up must not go red for it. Run
--update to prune, and review the diff like any other.

Usage:
    python3 tools/lint-baseline.py            # check (what CI runs); exit 1 on a new finding
    python3 tools/lint-baseline.py --update   # rewrite the baseline from the current tree

Tool binaries are found on PATH, or via $RUFF / $SHELLCHECK. VERSIONS ARE PINNED IN CI
(.github/workflows/lint.yml) and matter: Ubuntu's own shellcheck is 0.8.0 and reports a different
set from the 0.10.0 this baseline was captured with, so an unpinned runner would fail on findings
nobody introduced.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "lint-baseline.txt"

RUFF = os.environ.get("RUFF", "ruff")
SHELLCHECK = os.environ.get("SHELLCHECK", "shellcheck")

# ruff --output-format concise: "path/to/file.py:12:5: F401 [*] message"
_RUFF_RE = re.compile(r"^(?P<file>[^:]+):\d+:\d+:\s+(?P<code>[A-Z]+\d+)\b")
# Any ruff diagnostic line at all, coded or not - same "file:line:col: " shape _RUFF_RE keys off,
# but without requiring the rule-code tail. ruff 0.16.3 reports a file that doesn't even PARSE as
# "path:L:C: invalid-syntax: <msg>" with NO rule code (E999 is gone: "Syntax errors will always be
# shown regardless of whether this rule is selected" - ruff.toml's E9 comment is stale on this
# point, but ruff.toml isn't ours to fix here). _RUFF_RE never matches that line, so before this
# regex existed the Counter stayed empty and main() printed OK and exit 0 while ruff itself exited
# 1 - a file that fails to parse passed the gate green. Used to tell "a line _RUFF_RE could not
# parse" apart from "no output at all" (see ruff_findings()).
_RUFF_DIAG_RE = re.compile(r"^(?P<file>[^:]+):\d+:\d+:\s+\S")
# ruff's own tally, e.g. "Found 3 errors." - printed whenever it found ANYTHING, absent on a clean
# "All checks passed!" run. Cross-checking it against the count this script parsed is a second,
# independent net: it would catch a future ruff output shape that isn't even "file:line:col:"
# shaped, which _RUFF_DIAG_RE can't recognise as a diagnostic to begin with.
_RUFF_TRAILER_RE = re.compile(r"^Found (\d+) error")
# shellcheck -f gcc:      "path/to/file.sh:12:5: note: message [SC2034]"
_SC_RE = re.compile(r"^(?P<file>[^:]+):\d+:\d+:.*\[(?P<code>SC\d+)\]\s*$")


def _run(argv: list, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, **kw)
    except FileNotFoundError:
        sys.exit(f"lint-baseline: {argv[0]!r} not found. Install it, or set $RUFF / $SHELLCHECK.")


def ruff_findings() -> Counter:
    # Rule selection lives in ruff.toml so the CLI and an editor agree. --no-cache keeps a stale
    # .ruff_cache from making a run non-reproducible.
    res = _run([RUFF, "check", "--output-format", "concise", "--no-cache", "."])
    if res.returncode not in (0, 1):          # 1 = findings exist, anything else is a real failure
        sys.exit(f"lint-baseline: ruff failed ({res.returncode}):\n{res.stderr}")
    out: Counter = Counter()
    counted = 0
    trailer_n = None
    for raw in res.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _RUFF_RE.match(line)
        if m:
            out[(m.group("file"), m.group("code"))] += 1
            counted += 1
            continue
        t = _RUFF_TRAILER_RE.match(line)
        if t:
            trailer_n = int(t.group(1))
            continue
        if _RUFF_DIAG_RE.match(line):
            # Shaped like a diagnostic (file:line:col: ...) but _RUFF_RE couldn't parse it - the
            # exact shape of the invalid-syntax hole this function exists to close. Fail loudly
            # instead of silently dropping it; see _RUFF_DIAG_RE's comment above.
            sys.exit("lint-baseline: ruff emitted a diagnostic line this script can't parse "
                      f"(no rule code matched): {line!r}\n"
                      "Update _RUFF_RE / _RUFF_DIAG_RE in tools/lint-baseline.py to cover it.")
        # else: other chrome ("[*] N fixable with the `--fix` option.", "All checks passed!") - ignore.
    if trailer_n is not None and trailer_n != counted:
        sys.exit(f"lint-baseline: ruff reported 'Found {trailer_n} error(s)' but this script only "
                  f"counted {counted} - a finding is being silently dropped by the parsing above.")
    return out


def shell_files() -> list:
    # Plain "git ls-files *.sh" defaults to --cached (index-tracked only), but ruff walks the
    # whole working TREE - so a brand-new, not-yet-committed .sh is invisible here, --update
    # can't record its baseline, and the file is born RED in CI the moment it IS committed.
    # --others --exclude-standard adds untracked-but-present files to match. --cached and
    # --others can both name the same relative path (e.g. right after `git add`, before commit),
    # so de-duplicate below.
    res = _run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.sh"])
    if res.returncode != 0:
        sys.exit(f"lint-baseline: git ls-files failed:\n{res.stderr}")
    seen: set = set()
    files: list = []
    for f in res.stdout.split("\0"):
        if not f or f in seen:
            continue
        seen.add(f)
        # --cached reflects the INDEX, not the worktree: a tracked .sh deleted from disk but not
        # yet `git rm`/staged is still listed. shellcheck then can't open it and exits 2, which
        # used to take the whole run down with it ("returncode not in (0, 1)" -> fatal exit
        # below). Only hand shellcheck paths that actually exist.
        if (ROOT / f).is_file():
            files.append(f)
    return files


def shellcheck_findings() -> Counter:
    files = shell_files()
    if not files:
        return Counter()
    res = _run([SHELLCHECK, "-f", "gcc", *files])
    if res.returncode not in (0, 1):
        sys.exit(f"lint-baseline: shellcheck failed ({res.returncode}):\n{res.stderr}")
    out: Counter = Counter()
    for line in res.stdout.splitlines():
        m = _SC_RE.match(line.strip())
        if m:
            out[(m.group("file"), m.group("code"))] += 1
    return out


def current() -> Counter:
    c = ruff_findings()
    c.update(shellcheck_findings())
    return c


def load_baseline() -> Counter:
    out: Counter = Counter()
    if not BASELINE.is_file():
        return out
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        count, path, code = line.split("\t")
        out[(path, code)] = int(count)
    return out


def write_baseline(c: Counter) -> None:
    lines = [
        "# Frozen lint baseline. Regenerate with: python3 tools/lint-baseline.py --update",
        "# Format: <count>\\t<file>\\t<rule code>. CI fails when a count RISES or a new pair",
        "# appears; a count that falls only prints a note (prune it with --update).",
        "# Captured with the tool versions pinned in .github/workflows/lint.yml.",
    ]
    for (path, code), n in sorted(c.items()):
        lines.append(f"{n}\t{path}\t{code}")
    BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _suppress_hint(code: str) -> str:
    # The old message told every finding to add "# noqa: <CODE>", which does exactly nothing for
    # a shellcheck finding (verified: only "# shellcheck disable=SCxxxx" on the line ABOVE the
    # finding works) - and shellcheck findings are the MAJORITY of the baseline, so most readers
    # got advice that silently doesn't apply. No ruff rule code is prefixed "SC", so the prefix
    # alone tells the two apart.
    if code.startswith("SC"):
        return f"add `# shellcheck disable={code}` on the line ABOVE it"
    return f"add `# noqa: {code}  (reason)` on that line"


def main() -> int:
    update = "--update" in sys.argv[1:]
    now = current()
    if update:
        write_baseline(now)
        print(f"lint-baseline: wrote {BASELINE.relative_to(ROOT)} "
              f"({len(now)} file/rule pairs, {sum(now.values())} findings)")
        return 0

    base = load_baseline()
    if not BASELINE.is_file():
        sys.exit("lint-baseline: no baseline file. Run: python3 tools/lint-baseline.py --update")

    worse = sorted((k for k in now if now[k] > base.get(k, 0)),
                   key=lambda k: (k[0], k[1]))
    better = sorted((k for k in base if now.get(k, 0) < base[k]), key=lambda k: (k[0], k[1]))

    for path, code in better:
        print(f"note: {path} {code}: {base[(path, code)]} -> {now.get((path, code), 0)} "
              "(fixed; prune with --update)")
    if not worse:
        print(f"lint-baseline: OK, no new findings ({sum(now.values())} known, unchanged or fewer)")
        return 0

    print("\nNEW lint findings (these are not in tools/lint-baseline.txt):", file=sys.stderr)
    for path, code in worse:
        print(f"  {path}: {code} x{now[(path, code)]} (baseline {base.get((path, code), 0)}) "
              f"-> {_suppress_hint(code)}", file=sys.stderr)
    print("\nFix them, or - if a finding is deliberate - suppress it AT THE SITE (see the hint on\n"
          "each line above; shellcheck findings are the MAJORITY of this baseline and `# noqa`\n"
          "does nothing for them - only `# shellcheck disable=SCxxxx` on the line above works).\n"
          "Do NOT run `ruff check --fix` over this repo: unused-import fixes would delete the\n"
          "imports that REGISTER RPC methods. See ruff.toml.\n"
          "If the finding really is pre-existing and simply moved, re-run with --update and\n"
          "review the baseline diff in your commit.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
