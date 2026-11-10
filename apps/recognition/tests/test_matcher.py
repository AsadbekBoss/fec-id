import numpy as np

from recognition.matcher import Gallery, MatchPolicy, Template, decide
from recognition.types import Decision

from .conftest import unit

P = MatchPolicy(threshold=0.5, min_margin=0.05, uncertain_band=0.03)


def test_empty_gallery_is_unknown() -> None:
    r = decide([], P)
    assert r.decision is Decision.UNKNOWN and r.employee_id is None


def test_below_threshold_unknown() -> None:
    assert decide([("e1", 0.49)], P).decision is Decision.UNKNOWN


def test_confident_match_returns_employee() -> None:
    r = decide([("e1", 0.60), ("e2", 0.30)], P)
    assert r.decision is Decision.MATCHED and r.employee_id == "e1"


def test_uncertain_band_is_ambiguous_and_anonymous() -> None:
    r = decide([("e1", 0.51)], P)
    assert r.decision is Decision.AMBIGUOUS and r.employee_id is None and r.candidate_id == "e1"


def test_small_margin_is_ambiguous() -> None:
    r = decide([("e1", 0.70), ("e2", 0.67)], P)
    assert r.decision is Decision.AMBIGUOUS and r.employee_id is None


def test_boundary_exactly_threshold_plus_band_matches() -> None:
    assert decide([("e1", 0.54)], P).decision is Decision.MATCHED
    assert decide([("e1", 0.529)], P).decision is Decision.AMBIGUOUS


def test_gallery_best_template_per_employee_and_margin_vs_other_employee() -> None:
    g = Gallery("m@1", 3)
    g.load([
        Template("t1", "e1", "m@1", unit([1, 0, 0])),
        Template("t2", "e1", "m@1", unit([0.9, 0.1, 0])),  # same person: must not reduce the margin
        Template("t3", "e2", "m@1", unit([0, 1, 0])),
    ])
    ranked = g.scores(unit([1, 0, 0]))
    assert [e for e, _ in ranked] == ["e1", "e2"]
    assert decide(ranked, P).decision is Decision.MATCHED


def test_gallery_skips_other_models_and_wrong_dims_and_normalises() -> None:
    g = Gallery("m@1", 3)
    n = g.load([
        Template("t1", "e1", "other@1", unit([1, 0, 0])),
        Template("t2", "e2", "m@1", np.ones(4, np.float32)),
        Template("t3", "e3", "m@1", np.array([3, 0, 0], np.float32)),
    ])
    assert n == 1 and len(g) == 1 and g.employees == 1
    assert abs(g.scores(unit([1, 0, 0]))[0][1] - 1.0) < 1e-6
