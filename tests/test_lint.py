"""A name that does not exist must fail loudly, not quietly.

`thumbs.from_stock()` ranked its candidates through an undefined variable for
months. The bare `except Exception` around the call caught the NameError, logged
a fallback and shipped the wrong thumbnail every time; no test failed, because
every test that touched it was testing the fallback.

pyflakes finds that class of fault in a second without running anything. This is
deliberately narrow: it gates on undefined names and unreachable bindings, not
on style or unused imports, so it stays a signal worth reading.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyflakes", reason="pyflakes is in requirements-dev.txt")

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("vidsmith", "web", "tests")

# The faults worth failing a build over: a name that is not defined, a name used
# before it is assigned, and a nonlocal or global that is never actually bound.
FATAL = ("undefined name", "is never assigned in scope",
         "local variable", "before assignment")


def _pyflakes(*targets: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *targets],
        cwd=ROOT, capture_output=True, text=True,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_nothing_uses_a_name_that_does_not_exist():
    findings = [line for line in _pyflakes(*PACKAGES)
                if any(fault in line for fault in FATAL)]
    assert not findings, "pyflakes found names that cannot resolve:\n" + "\n".join(findings)


def test_the_gate_actually_catches_an_undefined_name(tmp_path):
    """A guard that cannot fail is not a guard; prove this one can."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f():\n    return not_a_real_name\n", encoding="utf-8")
    findings = _pyflakes(str(bad))
    assert any("undefined name" in line for line in findings), findings
