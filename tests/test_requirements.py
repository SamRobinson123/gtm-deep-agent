"""requirements.txt must be real.

The file did not exist until 2026-08-12 — matplotlib and reportlab were
installed ad hoc and a fresh clone could not run anything. The v2 agent edits
its own pipeline code, so the declared dependency list has to track what the
code actually imports, mechanically.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# import name -> PyPI distribution name, where they differ
DIST_OF = {
    "dotenv": "python-dotenv",
    "claude_agent_sdk": "claude-agent-sdk",
    "azure": "azure-identity",
    "PIL": "pillow",
}

# stdlib-or-local names that never belong in requirements
LOCAL = {"agent", "pipeline", "gtm_ui", "tests", "main"}


def declared() -> set[str]:
    out = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(line.split("==")[0].strip().lower())
    return out


def imported() -> set[str]:
    """Every top-level module imported anywhere in agent/, pipeline/, gtm_ui/."""
    names = set()
    for pkg in ("agent", "pipeline", "gtm_ui"):
        for py in (ROOT / pkg).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names.add(node.module.split(".")[0])
    return names


def test_every_third_party_import_is_declared():
    """A module the code imports but requirements.txt does not name is exactly
    how "works on my machine" happens — and how matplotlib went missing."""
    missing = []
    for name in sorted(imported()):
        if name in LOCAL or name in sys.stdlib_module_names:
            continue
        dist = DIST_OF.get(name, name).lower()
        if dist not in declared():
            missing.append(f"{name} (as {dist})")
    assert not missing, f"imported but not in requirements.txt: {', '.join(missing)}"


def test_the_pdf_stack_is_declared():
    """The capability §1 exists to add. matplotlib covers chart PDFs natively;
    reportlab covers text/table reports."""
    d = declared()
    assert "reportlab" in d
    assert "matplotlib" in d
    assert "openpyxl" in d
