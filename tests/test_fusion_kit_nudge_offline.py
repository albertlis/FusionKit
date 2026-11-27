"""hooks/fusion_kit_nudge.py load_index: per-card handling. Defect 4. Offline, hook loaded from file path (hooks/ is not a package)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "fusion_kit_nudge.py"
INDEX_NAME = "fusion_kit_api.md"
HEAD = "| helper | signature | replaces raw adsk |\n|---|---|---|\n"


def _hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fusion_kit_nudge_under_test", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docs(tmp_path: Path, index: str) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    (docs / "api").mkdir(parents=True)
    (docs / INDEX_NAME).write_text(index, encoding="utf-8")
    return docs / INDEX_NAME, docs / "api"


def test_good_card_loads_when_sibling_card_is_invalid_utf8(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hook = _hook()
    index, api = _docs(tmp_path, "# index\n")
    (api / "good.md").write_text(HEAD + "| good.fn | fn(x) | `rawToken` |\n", encoding="utf-8")
    (api / "bad.md").write_bytes(b"\xff\xfe| broken | bytes |\n")
    loaded = hook.load_index([index])
    assert loaded is not None and loaded[0] == {"rawToken": ["good.fn"]}
    assert "fusion_kit_nudge: skipped docs/api/bad.md: UnicodeDecodeError" in capsys.readouterr().err


def test_unreadable_card_skipped_others_load(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hook = _hook()
    index, api = _docs(tmp_path, "# index\n")
    (api / "good.md").write_text(HEAD + "| good.fn | fn(x) | `rawToken` |\n", encoding="utf-8")
    (api / "dirlike.md").mkdir()  # glob matches it, read_text raises an OSError (IsADirectoryError / PermissionError)
    loaded = hook.load_index([index])
    assert loaded is not None and loaded[0] == {"rawToken": ["good.fn"]}
    assert "fusion_kit_nudge: skipped docs/api/dirlike.md: " in capsys.readouterr().err


def test_empty_api_dir_uses_index_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hook = _hook()
    index, _ = _docs(tmp_path, "# index\n\n" + HEAD + "| old.fn | fn() | `oldToken` |\n")
    loaded = hook.load_index([index])
    assert loaded is not None and loaded[0] == {"oldToken": ["old.fn"]}
    assert capsys.readouterr().err == ""


def test_index_only_old_checkout_without_api_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hook = _hook()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / INDEX_NAME).write_text(HEAD + "| old.fn | fn() | `oldToken` |\n", encoding="utf-8")
    loaded = hook.load_index([docs / INDEX_NAME])
    assert loaded is not None and loaded[0] == {"oldToken": ["old.fn"]}
    assert capsys.readouterr().err == ""


def test_invalid_index_falls_through_to_next_candidate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hook = _hook()
    bad_root = tmp_path / "plugin"
    (bad_root / "docs").mkdir(parents=True)
    (bad_root / "docs" / INDEX_NAME).write_bytes(b"\xff\xfe")
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / INDEX_NAME).write_text(HEAD + "| ok.fn | fn() | `okToken` |\n", encoding="utf-8")
    loaded = hook.load_index([bad_root / "docs" / INDEX_NAME, repo / "docs" / INDEX_NAME])
    assert loaded is not None and loaded[0] == {"okToken": ["ok.fn"]}
    assert "fusion_kit_nudge: skipped docs/fusion_kit_api.md: UnicodeDecodeError" in capsys.readouterr().err
