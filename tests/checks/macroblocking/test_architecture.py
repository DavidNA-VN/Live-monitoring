from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_macroblocking_domain_does_not_depend_on_other_checks():
    forbidden = (
        "checks.black_screen",
        "checks.audio_loss",
        "checks.video_freeze",
    )
    root = PROJECT_ROOT / "src" / "checks" / "macroblocking"
    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            module for module in _imports(path) if module.startswith(forbidden)
        )
        for path in root.glob("*.py")
    }
    assert not {path: imports for path, imports in violations.items() if imports}


def test_presentation_does_not_import_private_macroblocking_modules():
    forbidden = (
        "checks.macroblocking",
        "detectors.macroblocking",
        "policies.macroblocking",
    )
    root = PROJECT_ROOT / "src" / "presentation"
    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            module for module in _imports(path) if module.startswith(forbidden)
        )
        for path in root.rglob("*.py")
    }
    assert not {path: imports for path, imports in violations.items() if imports}
