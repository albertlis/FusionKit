"""Entry-point runner for Fusion 360 part scripts.

Handles the Fusion module-cache problem: Fusion caches sys.modules across runs,
so a re-run of a modified part module picks up the old code unless the package
entries are purged first (docs/gotchas.md: module cache).

Usage (from a Fusion script or MCP execute):
    from fusion_kit.loader import run_part
    run_part(r"C:\\...\\my_part\\my_part.py")

Project layout assumed: <project_root>/<pkg>/<pkg>.py, optionally with entries one
level deeper in <pkg>/anim/ or <pkg>/parts/. Other layouts: pass project_root.
"""

import os
import sys
from typing import Any

# FusionKit root: one level above this file (fusion_kit/)
_FK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Entry sub-directories that live inside a part package, not at its top
_PKG_SUBDIRS = frozenset({"anim", "parts"})


def _package_dir(entry_dir: str) -> str:
    """Top directory of the part package that owns entry_dir."""
    if os.path.basename(entry_dir).lower() in _PKG_SUBDIRS:
        return os.path.dirname(entry_dir)
    return entry_dir


def _package_top(pkg_dir: str, root: str) -> str | None:
    """First path component of pkg_dir below root, or None when pkg_dir is not under root."""
    try:
        rel = os.path.relpath(pkg_dir, root)  # ValueError across drives
    except ValueError:
        return None
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.split(os.sep)[0]


def run_part(entry_path: str, context: str = "", project_root: str | None = None) -> None:
    """Purge stale module caches, ensure paths on sys.path, exec entry file, call run(context).

    entry_path:   absolute path to the part entry script (must define def run(_context)).
    context:      passed through to run() unchanged.
    project_root: directory that contains the part package; default = parent of the
                  package dir (see module docstring for the assumed layout).

    Purges sys.modules for:
    - "fusion_kit" and "fusion_kit.*" (ensures latest helpers)
    - the entry's top-level package name
    - "parts" and "parts.*"
    - any module whose __file__ is under the entry's directory (flat imports)

    sys.path: FusionKit root, project root and entry dir are added for the run only and
    removed afterwards (no stale 'helpers' from a previous package). Entries already
    present are left alone.
    """
    entry_path = os.path.abspath(entry_path)
    entry_dir = os.path.dirname(entry_path)
    pkg_dir = _package_dir(entry_dir)
    root = os.path.abspath(project_root) if project_root else os.path.dirname(pkg_dir)
    pkg_top = _package_top(pkg_dir, root)

    # Add paths for this run; normcase: Windows paths are case-insensitive.
    # Inserted at 0 in reverse priority -> final order FK root, project root, entry dir,
    # so <pkg>/<pkg>.py does not shadow the <pkg> package.
    added: list[str] = []
    for p in (entry_dir, root, _FK_ROOT):
        if os.path.normcase(p) not in {os.path.normcase(q) for q in sys.path}:
            sys.path.insert(0, p)
            added.append(p)

    try:
        # Purge stale Fusion module cache (importlib.reload fails 'spec not found')
        entry_dir_prefix = os.path.normcase(entry_dir) + os.sep

        def _under_entry(mod: object) -> bool:
            f = getattr(mod, "__file__", None)
            return f is not None and os.path.normcase(os.path.abspath(f)).startswith(entry_dir_prefix)

        stale = [
            n
            for n, mod in list(sys.modules.items())
            if n in {"parts", "fusion_kit"}
            or n.startswith(("parts.", "fusion_kit."))
            or (pkg_top is not None and (n == pkg_top or n.startswith(pkg_top + ".")))
            or _under_entry(mod)
        ]
        for n in stale:
            del sys.modules[n]

        ns: dict[str, Any] = {
            "__file__": entry_path,
            "__name__": "__fusion_entry__",
            "__builtins__": __builtins__,
        }
        with open(entry_path, encoding="utf-8") as f:
            exec(compile(f.read(), entry_path, "exec"), ns)  # noqa: S102
        ns["run"](context)  # entry must define def run(_context)
    finally:
        for p in added:
            if p in sys.path:  # run() may have removed it already; never mask the original exception
                sys.path.remove(p)
