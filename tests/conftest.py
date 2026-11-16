"""Shared offline fixtures: adsk stubs for fusion_kit imports, ObjectCollection / Point3D fakes."""

import os
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "adsk" not in sys.modules:
    _core_stub = MagicMock(name="adsk.core")
    _fusion_stub = MagicMock(name="adsk.fusion")
    sys.modules.update(
        {
            "adsk": MagicMock(name="adsk", core=_core_stub, fusion=_fusion_stub),
            "adsk.core": _core_stub,
            "adsk.fusion": _fusion_stub,
        }
    )

_CORE: Any = sys.modules["adsk.core"]


@pytest.fixture
def object_collection() -> Iterator[MagicMock]:
    """ObjectCollection.create returns one MagicMock collection; default restored afterwards."""
    coll = MagicMock(name="coll")
    _CORE.ObjectCollection.create.return_value = coll
    yield coll
    _CORE.ObjectCollection.create.return_value = MagicMock(name="default_coll")


@pytest.fixture
def point3d_xyz() -> Iterator[None]:
    """Point3D.create returns plain xyz (cm) so tests can read the mapped coordinates."""
    _CORE.Point3D.create.side_effect = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
    yield
    _CORE.Point3D.create.side_effect = None
