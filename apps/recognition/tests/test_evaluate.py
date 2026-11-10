import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from recognition import evaluate
from recognition.engine import RecognitionEngine
from recognition.evaluate import ProbeRecord, Rates, recommend, score, sweep
from recognition.matcher import Gallery, MatchPolicy
from recognition.quality import QualityLimits

from .conftest import MODEL_ID, FakeDetector, frontal_detection

P = MatchPolicy(0.5, 0.05, 0.03)


def rec(subject: str, genuine: bool, ranked: list[tuple[str, float]], acquired: bool = True) -> ProbeRecord:
    return ProbeRecord(subject, genuine, {"lighting": "good", "angle": "frontal", "distance_m": "3"},
                       acquired, [] if acquired else ["face_too_small"], ranked, {"detect": 10.0, "embed": 5.0})


def test_score_counts_each_outcome() -> None:
    records = [
        rec("a", True, [("a", 0.8), ("b", 0.2)]),  # correct match
        rec("a", True, [("a", 0.51)]),  # ambiguous -> false reject, held for review
        rec("a", True, [("b", 0.9), ("a", 0.3)]),  # misidentification
        rec("a", True, [], acquired=False),  # failure to acquire
        rec("x", False, [("a", 0.9)]),  # impostor accepted
        rec("y", False, [("a", 0.2)]),  # impostor rejected
    ]
    r = score(records, P)
    assert (r.attempts, r.fta, r.false_reject, r.misidentified) == (4, 1, 3, 1)
    assert (r.impostor_attempts, r.false_accept, r.ambiguous) == (2, 1, 1)
    d = r.as_dict()
    assert d["FPIR_false_accept"] == {"errors": 1, "n": 2, "rate": 0.5}
    assert d["upper_bound_95_if_zero_FPIR"] is None


def test_rule_of_three_when_no_false_accepts() -> None:
    r = Rates(impostor_attempts=300)
    assert r.as_dict()["upper_bound_95_if_zero_FPIR"] == 0.01


def test_sweep_is_monotonic_and_recommendation_needs_enough_impostors() -> None:
    records = [rec("a", True, [("a", s)]) for s in (0.45, 0.6, 0.7)] + [rec("x", False, [("a", 0.4)])]
    rows = sweep(records, P)
    fpir = [r["FPIR"] for r in rows]
    fnir = [r["FNIR"] for r in rows]
    assert fpir == sorted(fpir, reverse=True) and fnir == sorted(fnir)
    best = recommend(rows, impostors=1, target_fpir=0.001)
    assert best["recommended_threshold"] is not None and best["recommended_threshold"] >= 0.38
    assert best["impostor_attempts_needed_to_demonstrate_target"] == 3000
    assert best["statistically_sufficient"] is False


# --- end-to-end harness run with a deterministic fake model -------------------------------------

COLORS = {"a": (200, 60, 60), "b": (60, 200, 60), "c": (60, 60, 200)}  # BGR tint per subject


class ColorEmbedder:
    """Fake 'identity' = colour tint of the crop. Deterministic, no faces needed."""

    model_id, dim = MODEL_ID, 4

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        m = aligned_bgr[16:96, 16:96].reshape(-1, 3).mean(axis=0) - 127
        v = np.array([*m, 1.0], np.float32)
        return v / np.linalg.norm(v)


def _write(folder: Path, name: str, subject: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    img = np.clip(np.array(COLORS[subject]) + rng.integers(-30, 30, (600, 600, 3)), 0, 255).astype(np.uint8)
    cv2.imwrite(str(folder / name), img)


def _dataset(folder: Path, consent: bool = True) -> None:
    rows = ["image,subject_id,split,lighting,angle,distance_m,consent_id"]
    plan = [("a1.png", "a", "enroll"), ("b1.png", "b", "enroll"), ("a2.png", "a", "probe"),
            ("b2.png", "b", "probe"), ("c1.png", "c", "probe")]
    for i, (name, subj, split) in enumerate(plan):
        _write(folder, name, subj, i)
        rows.append(f"{name},{subj},{split},good,frontal,3,{'C-' + subj if consent else ''}")
    (folder / "manifest.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _engine() -> RecognitionEngine:
    return RecognitionEngine(FakeDetector([frontal_detection()]), ColorEmbedder(), Gallery(MODEL_ID, 4),
                             P, QualityLimits())


def test_run_end_to_end_and_report(tmp_path: Path) -> None:
    _dataset(tmp_path)
    report = evaluate.run(_engine(), tmp_path)
    o = report.overall
    assert o["genuine_attempts"] == 2 and o["impostor_attempts"] == 1
    assert o["FNIR_false_reject"]["errors"] == 0 and o["FPIR_false_accept"]["errors"] == 0
    assert report.enrollment["templates"] == 2
    assert set(report.by_condition) == {"lighting", "angle", "distance_m"}
    assert any("impostor attempts" in c for c in report.caveats)
    assert any("not a guarantee" in c for c in report.caveats)
    jp, mp = evaluate.write_report(report, tmp_path / "out")
    assert json.loads(jp.read_text(encoding="utf-8"))["overall"]["genuine_attempts"] == 2
    md = mp.read_text(encoding="utf-8")
    assert "FPIR / false accept" in md and "## Caveats" in md


def test_manifest_without_consent_is_refused(tmp_path: Path) -> None:
    _dataset(tmp_path, consent=False)
    with pytest.raises(evaluate.DatasetError, match="consent_id is required"):
        evaluate.read_manifest(tmp_path)


def test_manifest_missing_columns_and_images(tmp_path: Path) -> None:
    (tmp_path / "manifest.csv").write_text("image,subject_id\nx.png,a\n", encoding="utf-8")
    with pytest.raises(evaluate.DatasetError, match="missing columns"):
        evaluate.read_manifest(tmp_path)
    (tmp_path / "manifest.csv").write_text(
        "image,subject_id,split,lighting,angle,distance_m,consent_id\nx.png,a,enroll,good,frontal,3,C1\n",
        encoding="utf-8")
    with pytest.raises(evaluate.DatasetError, match="image not found"):
        evaluate.read_manifest(tmp_path)
