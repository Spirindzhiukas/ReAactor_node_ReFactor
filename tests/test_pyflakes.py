"""Static analysis gate: pyflakes must report NO undefined names.

This class of bug (references to names that don't exist — e.g. a function that
a rename or a silently-skipped patch forgot to create) only surfaces at
runtime inside node execution, which is exactly where users hit it. This test
fails on any "undefined name" finding. Benign pyflakes categories (unused
imports/variables, f-string style notes, star-import advisories) are ignored.

Usage: python tests/test_pyflakes.py
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

IGNORE_SUBSTRINGS = (
    "unable to detect undefined names",              # star-import advisory header
    "may be undefined, or defined from star imports",  # vendored CodeFormer arch
)


def main():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        print("SKIP: pyflakes not installed (pip install pyflakes) — gate not enforced here.")
        return 0

    targets = [str(REPO / "rfactor"), str(REPO / "nodes.py"), str(REPO / "__init__.py"), str(REPO / "install.py")]
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes"] + targets,
        capture_output=True, text=True,
    )
    lines = [l for l in (result.stdout + result.stderr).splitlines() if l.strip()]
    bad = [
        l for l in lines
        if ("undefined name" in l or "SYNTAX ERROR" in l or "syntax error" in l)
        and not any(sub in l for sub in IGNORE_SUBSTRINGS)
    ]

    # informational: show the ignored/notice lines
    for l in lines:
        if l not in bad:
            print(f"  note: {l}")

    if bad:
        print(f"\nFAIL — {len(bad)} undefined reference(s):")
        for l in bad:
            print("  " + l)
        return 1
    print("OK — no undefined names in rfactor/, nodes.py, __init__.py, install.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
