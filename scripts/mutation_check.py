"""Mutation check: prove the test suite notices a disabled guard.

1. Copy the project (without .git) to a temporary directory.
2. Baseline copy: run the tests unchanged; they must pass.
3. For each mutant, a fresh copy: disable one guard in quotecheck/fetch.py
   (each edited text must appear exactly once, or the script fails), run
   the tests, and require at least one failure.

Mutants: the SSRF address guard, and the size cap applied after
decompression (both its bounded read and its check).

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
MUTANTS = [
    (
        "SSRF guard disabled",
        [("    if not _is_public_address(addr):",
          "    if False:  # MUTATION: SSRF guard disabled")],
    ),
    (
        "size cap after decompression removed",
        [("out = zlib.decompressobj(wbits).decompress(body, max_bytes + 1)",
          "out = zlib.decompressobj(wbits).decompress(body)  # MUTATION"),
         ("            if len(out) > max_bytes:",
          "            if False:  # MUTATION: no cap after decompression")],
    ),
]


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

        worst = 0
        for i, (name, edits) in enumerate(MUTANTS):
            mutant = copy_project(os.path.join(tmp, f"mutant{i}"))
            fetch_py = os.path.join(mutant, "quotecheck", "fetch.py")
            with open(fetch_py, encoding="utf-8", newline="") as fh:
                src = fh.read()
            for old, new in edits:
                count = src.count(old)
                if count != 1:
                    print(f"{name}: expected exactly one {old.strip()!r}, found {count}", file=sys.stderr)
                    return 2
                src = src.replace(old, new)
            with open(fetch_py, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)

            mut = run_tests(mutant)
            failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", mut.stderr, re.M)))
            print(f"mutant ({name}): exit {mut.returncode}; {mut.stderr.strip().splitlines()[-1]}")
            for test in failed:
                print(f"  failed: {test}")
            if mut.returncode == 0:
                print(f"MUTANT SURVIVED: {name} did not fail any test", file=sys.stderr)
                worst = 1
            else:
                print(f"MUTANT KILLED: {name} fails the suite")
        return worst


if __name__ == "__main__":
    sys.exit(main())
