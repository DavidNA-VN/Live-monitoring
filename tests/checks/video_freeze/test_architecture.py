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


def test_video_freeze_domain_does_not_depend_on_other_diseases():
    forbidden = ("checks.black_screen", "checks.audio_loss")
    freeze_root = PROJECT_ROOT / "src" / "checks" / "video_freeze"

    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            module
            for module in _imports(path)
            if module.startswith(forbidden)
        )
        for path in freeze_root.glob("*.py")
    }

    assert not {path: imports for path, imports in violations.items() if imports}


def test_presentation_does_not_import_private_freeze_domain_or_keys():
    forbidden = (
        "checks.video_freeze",
        "detectors.video_freeze",
        "policies.video_freeze",
    )
    presentation_root = PROJECT_ROOT / "src" / "presentation"

    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            module
            for module in _imports(path)
            if module.startswith(forbidden)
        )
        for path in presentation_root.rglob("*.py")
    }

    assert not {path: imports for path, imports in violations.items() if imports}
