"""Offline: bucketed dfam.match_faces == original O(F^2) implementation (kept verbatim below). Pure floats."""

from __future__ import annotations

import random

import pytest

from fusion_kit import dfam


def _old_sig_close(a, b, tol):
    (ac, aa), (bc, ba) = a, b
    return abs(aa - ba) <= tol and all(abs(ac[k] - bc[k]) <= tol for k in range(3))


def _old_match_faces(orig, copy, tol=dfam.SIG_TOL_CM):
    """Verbatim copy of the pre-bucketing dfam.match_faces."""

    def unique(j):
        return sum(_old_sig_close(o, copy[j], tol) for o in orig) == 1

    out = {}
    used = set()
    for i, o in enumerate(orig):
        cand = [j for j, c in enumerate(copy) if j not in used and _old_sig_close(o, c, tol)]
        if i in cand and unique(i):
            j = i
        elif len(cand) == 1 and unique(cand[0]):
            j = cand[0]
        else:
            continue
        out[i] = j
        used.add(j)
    return out


def _sig(rng: random.Random, tol: float, grid: float) -> tuple[tuple[float, float, float], float]:
    # coarse grid of `grid` steps: near-duplicates, exact-tol offsets and cell-boundary cases are common
    c = tuple(rng.randint(-4, 4) * grid + rng.choice((0.0, 1e-9, -1e-9)) for _ in range(3))
    return c, rng.randint(0, 3) * grid + 1.0


def _perturb(rng: random.Random, s, tol: float):
    c, a = s
    steps = (-tol, -tol / 2, 0.0, tol / 2, tol, 1.5 * tol, 2 * tol)
    return tuple(x + rng.choice(steps) for x in c), a + rng.choice(steps)


@pytest.mark.parametrize("seed", range(300))
def test_match_faces_equals_old_random(seed: int) -> None:
    rng = random.Random(seed)
    tol = dfam.SIG_TOL_CM
    orig = [_sig(rng, tol, tol / 2) for _ in range(rng.randint(1, 40))]
    copy = [_perturb(rng, s, tol) if rng.random() < 0.8 else _sig(rng, tol, tol / 2) for s in orig]
    rng.shuffle(copy)
    assert dfam.match_faces(orig, copy) == _old_match_faces(orig, copy)


@pytest.mark.parametrize("seed", range(50))
def test_match_faces_equals_old_custom_tol(seed: int) -> None:
    rng = random.Random(1000 + seed)
    tol = rng.choice((0.5, 0.01, 1e-6))
    orig = [_sig(rng, tol, tol) for _ in range(rng.randint(1, 25))]
    copy = [_perturb(rng, s, tol) for s in orig]
    assert dfam.match_faces(orig, copy, tol) == _old_match_faces(orig, copy, tol)


def test_match_faces_mapping_nontrivial() -> None:
    # guard: the random cases above must exercise real matches, not only empty dicts
    rng = random.Random(7)
    tol = dfam.SIG_TOL_CM
    total = 0
    for _ in range(100):
        orig = [_sig(rng, tol, tol / 2) for _ in range(20)]
        copy = [_perturb(rng, s, tol) for s in orig]
        total += len(dfam.match_faces(orig, copy))
    assert total > 100  # measured 353 matches over these 100 trials (guard against all-empty mappings)


def test_match_faces_tol_boundary_and_ambiguity() -> None:
    tol = dfam.SIG_TOL_CM
    base = ((0.0, 0.0, 0.0), 1.0)
    assert dfam.match_faces([base], [((tol, 0.0, 0.0), 1.0)]) == {0: 0}  # exactly tol: close
    assert dfam.match_faces([base], [((tol * 1.01, 0.0, 0.0), 1.0)]) == {}  # just outside
    assert dfam.match_faces([base], [((0.0, 0.0, 0.0), 1.0 + tol * 1.01)]) == {}  # area outside
    twins = [base, base]  # two originals on one copy face: ambiguous, not mapped
    assert dfam.match_faces(twins, [base]) == _old_match_faces(twins, [base]) == {}


def test_match_faces_rejects_nonpositive_tol() -> None:
    with pytest.raises(ValueError):
        dfam.match_faces([((0.0, 0.0, 0.0), 1.0)], [((0.0, 0.0, 0.0), 1.0)], 0.0)
