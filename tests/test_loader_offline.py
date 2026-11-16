"""Tests for fusion_kit.loader — pure Python, no adsk dependency."""

import os
import sys
import textwrap
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# normcase is identity on case-sensitive systems; the case tests only mean something on Windows.
requires_case_insensitive = pytest.mark.skipif(
    os.path.normcase("A") == "A",
    reason="needs case-insensitive path normcase (Windows)",
)


def test_loader_import_no_adsk() -> None:
    """loader must not import adsk at module level."""
    # Remove adsk from sys.modules to simulate Fusion-less environment
    adsk_mods = [k for k in sys.modules if k == "adsk" or k.startswith("adsk.")]
    saved = {k: sys.modules.pop(k) for k in adsk_mods}
    try:
        import importlib

        import fusion_kit.loader

        importlib.reload(fusion_kit.loader)
    finally:
        sys.modules.update(saved)


def test_utf8_entry(tmp_path: Path) -> None:
    """Entry file with UTF-8 'Ł' loads without UnicodeDecodeError."""
    from fusion_kit.loader import run_part

    entry = tmp_path / "utf8_entry.py"
    entry.write_text(
        "# Ł — UTF-8 marker\nrecords: list[str] = []\ndef run(ctx: str) -> None:\n    records.append(ctx)\n",
        encoding="utf-8",
    )
    run_part(str(entry), "hello")  # must not raise


def test_file_and_context(tmp_path: Path) -> None:
    """__file__ is set in exec namespace; context is passed through to run()."""
    from fusion_kit.loader import run_part

    result_file = tmp_path / "result.txt"
    entry = tmp_path / "part.py"
    entry.write_text(
        textwrap.dedent(f"""\
        # Ł marker
        def run(ctx: str) -> None:
            with open({str(result_file)!r}, 'w', encoding='utf-8') as f:
                f.write(__file__ + '\\n' + ctx)
        """),
        encoding="utf-8",
    )

    run_part(str(entry), "myctx")

    lines = result_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == str(entry), f"__file__ mismatch: {lines[0]!r}"
    assert lines[1] == "myctx", f"context mismatch: {lines[1]!r}"


def test_sibling_module_purge(tmp_path: Path) -> None:
    """Second run after modifying a sibling flat module sees the updated value (purge works)."""
    from fusion_kit.loader import run_part

    sibling = tmp_path / "sibling_mod.py"
    sibling.write_text("VALUE = 'first'\n", encoding="utf-8")

    result_file = tmp_path / "result.txt"
    entry = tmp_path / "entry.py"
    entry.write_text(
        textwrap.dedent(f"""\
        import sibling_mod
        def run(ctx: str) -> None:
            with open({str(result_file)!r}, 'w', encoding='utf-8') as f:
                f.write(sibling_mod.VALUE)
        """),
        encoding="utf-8",
    )

    run_part(str(entry), "")
    assert result_file.read_text(encoding="utf-8") == "first"

    # Modify sibling; second run must see new value
    sibling.write_text("VALUE = 'second'\n", encoding="utf-8")
    run_part(str(entry), "")
    assert result_file.read_text(encoding="utf-8") == "second", (
        "Module cache not purged — second run still sees old sibling value"
    )


def _write_entry(dir_: Path, body: str = "def run(ctx: str) -> None:\n    pass\n") -> str:
    """Write a no-op entry file into dir_ and return its path as str."""
    entry = dir_ / "entry.py"
    entry.write_text(body, encoding="utf-8")
    return str(entry)


def test_sys_path_restored_after_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry dir and repo root are removed from sys.path after run (no stale helpers for next package)."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    run_part(_write_entry(tmp_path), "")
    assert sys.path == before, "run_part leaked entries on sys.path"


def test_sys_path_restored_when_run_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.path cleanup runs in finally: an exception inside run() still restores sys.path."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    entry = _write_entry(tmp_path, "def run(ctx: str) -> None:\n    raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError, match="boom"):
        run_part(entry, "")
    assert sys.path == before, "sys.path not restored after exception in run()"


def test_run_error_not_masked_by_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run() removes its own sys.path entry, then raises: cleanup must skip it; original RuntimeError surfaces."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    entry = _write_entry(
        tmp_path,
        "import sys\ndef run(ctx: str) -> None:\n    sys.path.remove(ctx)\n    raise RuntimeError('boom')\n",
    )
    with pytest.raises(RuntimeError, match="boom"):
        run_part(entry, str(tmp_path))


@requires_case_insensitive
def test_existing_sys_path_entry_matched_case_insensitively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry dir already on sys.path with different case/slashes is not added twice and not removed."""
    from fusion_kit import loader

    variant = str(tmp_path).swapcase()  # same dir, different case; normcase makes them equal on Windows
    monkeypatch.setattr(sys, "path", [variant, loader._FK_ROOT, str(tmp_path.parent), *sys.path])
    before = list(sys.path)
    loader.run_part(_write_entry(tmp_path), "")
    assert sys.path == before, "case-variant duplicate added or user's sys.path entry removed"


def _make_project(root: Path, value: str) -> None:
    """<root>/demo_pkg/{demo_pkg.py, parts/dims.py, anim/anim.py}; entries read demo_pkg.parts.dims.VALUE."""
    pkg = root / "demo_pkg"
    (pkg / "parts").mkdir(parents=True, exist_ok=True)
    (pkg / "anim").mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "parts" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "parts" / "dims.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    body = textwrap.dedent("""\
        from demo_pkg.parts import dims
        def run(ctx: str) -> None:
            with open(ctx, 'w', encoding='utf-8') as f:
                f.write(dims.VALUE)
        """)
    (pkg / "demo_pkg.py").write_text(body, encoding="utf-8")
    (pkg / "anim" / "anim.py").write_text(body, encoding="utf-8")


@pytest.mark.parametrize("entry_rel", ["demo_pkg/demo_pkg.py", "demo_pkg/anim/anim.py"])
def test_project_package_import_and_purge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_rel: str) -> None:
    """Entry at <pkg>/<pkg>.py or <pkg>/anim/anim.py imports its package via the project root,
    and a second run sees an edited package module (package purged by name)."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    project = tmp_path / "project"
    out = tmp_path / "out.txt"
    _make_project(project, "first")
    entry = str(project / entry_rel)
    run_part(entry, str(out))
    assert out.read_text(encoding="utf-8") == "first"

    _make_project(project, "second")
    run_part(entry, str(out))
    assert out.read_text(encoding="utf-8") == "second", "package module not purged"
    for name in [n for n in sys.modules if n == "demo_pkg" or n.startswith("demo_pkg.")]:
        del sys.modules[name]


def test_explicit_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """project_root overrides the layout guess (entry two levels below the package)."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    project = tmp_path / "project"
    _make_project(project, "deep")
    deep = project / "demo_pkg" / "tools" / "nested"
    deep.mkdir(parents=True)
    entry = deep / "probe.py"
    entry.write_text((project / "demo_pkg" / "demo_pkg.py").read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "out.txt"
    run_part(str(entry), str(out), project_root=str(project))
    assert out.read_text(encoding="utf-8") == "deep"
    for name in [n for n in sys.modules if n == "demo_pkg" or n.startswith("demo_pkg.")]:
        del sys.modules[name]


@requires_case_insensitive
def test_purge_matches_module_file_case_insensitively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Module whose __file__ is under entry dir in different case is purged from sys.modules."""
    from fusion_kit.loader import run_part

    monkeypatch.setattr(sys, "path", list(sys.path))
    fake = types.ModuleType("flat_stale_mod")
    fake.__file__ = str(tmp_path / "flat_stale_mod.py").swapcase()
    monkeypatch.setitem(sys.modules, "flat_stale_mod", fake)
    run_part(_write_entry(tmp_path), "")
    assert "flat_stale_mod" not in sys.modules, "case-variant __file__ not purged"
