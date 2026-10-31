"""Mutation check: prove the test suite notices a disabled SSRF guard.

1. Copy the project (without .git) to two temporary directories.
2. Baseline copy: run the tests unchanged; they must pass.
3. Mutant copy: replace the address check in quotecheck/fetch.py with a
   no-op (exactly one replacement, or the script fails), run the tests,
   and require at least one failure.

Usage: python scripts/mutation_check.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD_LINE = "    if not _is_public_address(addr):"
MUTANT_LINE = "    if False:  # MUTATION: SSRF guard disabled"


def copy_project(dest: str) -> str:
    target = os.path.join(dest, "quotecheck")
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info"))
    return target


def run_tests(project: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=project, capture_output=True, text=True, env=env, timeout=600,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="quotecheck-mut-") as tmp:
        baseline = copy_project(os.path.join(tmp, "baseline"))
        base = run_tests(baseline)
        print(f"baseline: exit {base.returncode}; {base.stderr.strip().splitlines()[-1]}")
        if base.returncode != 0:
            print("baseline must pass before mutating", file=sys.stderr)
            print(base.stderr, file=sys.stderr)
            return 2

        mutant = copy_project(os.path.join(tmp, "mutant"))
        fetch_py = os.path.join(mutant, "quotecheck", "fetch.py")
        with open(fetch_py, encoding="utf-8", newline="") as fh:
            src = fh.read()
        count = src.count(GUARD_LINE)
        if count != 1:
            print(f"expected exactly one guard line, found {count}", file=sys.stderr)
            return 2
        with open(fetch_py, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.replace(GUARD_LINE, MUTANT_LINE))

        mut = run_tests(mutant)
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", mut.stderr, re.M)))
        print(f"mutant:   exit {mut.returncode}; {mut.stderr.strip().splitlines()[-1]}")
        for name in failed:
            print(f"  failed: {name}")
        if mut.returncode == 0:
            print("MUTANT SURVIVED: disabling the SSRF guard did not fail any test", file=sys.stderr)
            return 1
        print("MUTANT KILLED: disabling the SSRF guard fails the suite")
        return 0


if __name__ == "__main__":
    sys.exit(main())
