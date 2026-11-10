"""Offline evaluation on a CONSENTED dataset (see apps/recognition/EVALUATION_PROTOCOL.md).

Dataset: a folder with `manifest.csv` and images. Columns:
    image,subject_id,split,lighting,angle,distance_m,consent_id
* split = enroll | probe. Subjects with enroll images form the gallery. Probe images of
  subjects WITHOUT enroll images are impostor attempts (people the system must not identify).
* Every row needs a consent_id. Rows without one are refused (no consent, no processing).

Metrics (1:N identification, ISO/IEC 19795-1 terminology in brackets):
* FTA  failure to acquire: no face or quality gate failed.
* FNIR [false reject]: genuine probe not identified as its own subject (incl. FTA).
* MIR  misidentification: genuine probe MATCHED to a different subject (false acceptance).
* FPIR [false accept]: impostor probe MATCHED to anyone.
With zero observed errors in N attempts, the 95% upper bound is ~3/N (rule of three). Small
samples cannot demonstrate low error rates, and the report says so.
"""

from __future__ import annotations

import csv
import json
import platform
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from recognition.engine import RecognitionEngine, decode_image
from recognition.matcher import MatchPolicy, Template, decide
from recognition.types import Decision

REQUIRED = ("image", "subject_id", "split", "lighting", "angle", "distance_m", "consent_id")
CONDITIONS = ("lighting", "angle", "distance_m")


class DatasetError(ValueError):
    pass


@dataclass
class Row:
    image: str
    subject_id: str
    split: str
    lighting: str
    angle: str
    distance_m: str
    consent_id: str


def read_manifest(folder: Path) -> list[Row]:
    path = folder / "manifest.csv"
    if not path.is_file():
        raise DatasetError(f"{path} not found")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(f"manifest.csv is missing columns: {missing}")
        rows = [Row(**{k: (r.get(k) or "").strip() for k in REQUIRED}) for r in reader]
    for i, r in enumerate(rows, start=2):
        if not r.consent_id:
            raise DatasetError(f"manifest.csv line {i}: consent_id is required (no consent, no processing)")
        if r.split not in ("enroll", "probe"):
            raise DatasetError(f"manifest.csv line {i}: split must be enroll or probe")
        if not (folder / r.image).is_file():
            raise DatasetError(f"manifest.csv line {i}: image not found: {r.image}")
    return rows


@dataclass
class ProbeRecord:
    subject_id: str
    genuine: bool
    conditions: dict[str, str]
    acquired: bool
    quality_reasons: list[str]
    ranked: list[tuple[str, float]]
    timings_ms: dict[str, float]


@dataclass
class Rates:
    attempts: int = 0
    fta: int = 0
    false_reject: int = 0
    misidentified: int = 0
    impostor_attempts: int = 0
    false_accept: int = 0
    ambiguous: int = 0

    def as_dict(self) -> dict[str, object]:
        gen = self.attempts
        imp = self.impostor_attempts
        return {
            "genuine_attempts": gen,
            "impostor_attempts": imp,
            "FTA": _rate(self.fta, gen),
            "FNIR_false_reject": _rate(self.false_reject, gen),
            "MIR_misidentification": _rate(self.misidentified, gen),
            "FPIR_false_accept": _rate(self.false_accept, imp),
            "ambiguous_held_for_review": self.ambiguous,
            "upper_bound_95_if_zero_FPIR": round(3 / imp, 4) if imp and self.false_accept == 0 else None,
        }


def _rate(k: int, n: int) -> dict[str, object]:
    return {"errors": k, "n": n, "rate": round(k / n, 4) if n else None}


def score(records: list[ProbeRecord], policy: MatchPolicy) -> Rates:
    r = Rates()
    for p in records:
        decision = decide(p.ranked, policy) if p.acquired else None
        if p.genuine:
            r.attempts += 1
            if not p.acquired:
                r.fta += 1
                r.false_reject += 1
                continue
            if decision is None:  # unreachable: acquired probes always get a decision
                continue
            if decision.decision is Decision.MATCHED and decision.employee_id == p.subject_id:
                continue
            if decision.decision is Decision.MATCHED:
                r.misidentified += 1
            if decision.decision is Decision.AMBIGUOUS:
                r.ambiguous += 1
            r.false_reject += 1
        else:
            r.impostor_attempts += 1
            if p.acquired and decision is not None:
                if decision.decision is Decision.MATCHED:
                    r.false_accept += 1
                elif decision.decision is Decision.AMBIGUOUS:
                    r.ambiguous += 1
    return r


def sweep(
    records: list[ProbeRecord], base: MatchPolicy, lo: float = 0.2, hi: float = 0.8, step: float = 0.01
) -> list[dict[str, float | None]]:
    out: list[dict[str, float | None]] = []
    for t in np.arange(lo, hi + 1e-9, step):
        rr = score(records, MatchPolicy(round(float(t), 3), base.min_margin, base.uncertain_band))
        out.append({
            "threshold": round(float(t), 3),
            "FNIR": rr.false_reject / rr.attempts if rr.attempts else None,
            "FPIR": rr.false_accept / rr.impostor_attempts if rr.impostor_attempts else None,
            "MIR": rr.misidentified / rr.attempts if rr.attempts else None,
        })
    return out


def recommend(sweep_rows: list[dict[str, float | None]], impostors: int, target_fpir: float) -> dict[str, object]:
    ok = [r for r in sweep_rows if (f := r["FPIR"]) is not None and f <= target_fpir and (r["MIR"] or 0) == 0]
    best = min(ok, key=lambda r: (r["FNIR"] if r["FNIR"] is not None else 1.0, r["threshold"])) if ok else None
    needed = int(np.ceil(3 / target_fpir))
    return {
        "target_FPIR": target_fpir,
        "recommended_threshold": best["threshold"] if best else None,
        "at_recommendation": best,
        "impostor_attempts": impostors,
        "impostor_attempts_needed_to_demonstrate_target": needed,
        "statistically_sufficient": impostors >= needed,
    }


def latency_summary(records: list[ProbeRecord]) -> dict[str, dict[str, float]]:
    by: dict[str, list[float]] = defaultdict(list)
    for p in records:
        for k, v in p.timings_ms.items():
            by[k].append(v)
    return {
        k: {"n": len(v), "p50": round(float(np.percentile(v, 50)), 2), "p95": round(float(np.percentile(v, 95)), 2),
            "p99": round(float(np.percentile(v, 99)), 2), "max": round(max(v), 2)}
        for k, v in by.items() if v
    }


@dataclass
class EvaluationReport:
    dataset: dict[str, object]
    models: dict[str, str]
    policy: dict[str, float]
    enrollment: dict[str, object]
    overall: dict[str, object]
    by_condition: dict[str, dict[str, dict[str, object]]]
    threshold_sweep: list[dict[str, float | None]]
    recommendation: dict[str, object]
    latency_ms: dict[str, dict[str, float]]
    environment: dict[str, object]
    caveats: list[str] = field(default_factory=list)


def run(engine: RecognitionEngine, folder: Path, target_fpir: float = 0.001) -> EvaluationReport:
    rows = read_manifest(folder)
    enroll = [r for r in rows if r.split == "enroll"]
    probes = [r for r in rows if r.split == "probe"]
    enrolled_subjects = sorted({r.subject_id for r in enroll})

    # Enrollment (failure-to-enrol is reported, never hidden).
    templates: list[Template] = []
    fte: dict[str, str] = {}
    for r in enroll:
        img = decode_image((folder / r.image).read_bytes(), 1920)
        vec, _, err = engine.embed_best_face(img)
        if vec is None:
            fte[r.image] = err or "unknown"
            continue
        templates.append(Template(r.image, r.subject_id, engine.embedder.model_id, vec))
    engine.gallery.load(templates)

    records: list[ProbeRecord] = []
    for r in probes:
        img = decode_image((folder / r.image).read_bytes(), 1920)
        res = engine.process(img, match=False)
        # One subject per probe image: evaluate the largest detected face.
        face = max(res.faces, key=lambda f: f.detection.box[2], default=None)
        timings = dict(res.timings_ms)
        acquired = face is not None and face.quality.passed
        ranked: list[tuple[str, float]] = []
        if acquired and face is not None:
            timings.update(face.timings_ms)
            emb = res.embeddings[res.faces.index(face)]
            ranked = engine.gallery.scores(emb) if emb is not None else _rank_face(engine, img, face)
        records.append(ProbeRecord(
            subject_id=r.subject_id,
            genuine=r.subject_id in enrolled_subjects,
            conditions={c: getattr(r, c) for c in CONDITIONS},
            acquired=acquired,
            quality_reasons=face.quality.reasons if face else ["no_face"],
            ranked=ranked,
            timings_ms=timings,
        ))

    by_condition: dict[str, dict[str, dict[str, object]]] = {}
    for c in CONDITIONS:
        groups: dict[str, list[ProbeRecord]] = defaultdict(list)
        for p in records:
            groups[p.conditions[c]].append(p)
        by_condition[c] = {k: score(v, engine.policy).as_dict() for k, v in sorted(groups.items())}

    sw = sweep(records, engine.policy)
    overall = score(records, engine.policy)
    caveats = [
        "Results describe THIS dataset, camera, lens, mounting and lighting only. They are not a guarantee.",
        "Recognition at 3-5 m depends on face pixel size (lens focal length, resolution). Faces below "
        "the minimum size are rejected by design (FTA), not guessed.",
    ]
    if overall.impostor_attempts < 300:
        bound = 3 / max(overall.impostor_attempts, 1)
        caveats.append(f"Only {overall.impostor_attempts} impostor attempts: FPIR below ~{bound:.3f} cannot be demonstrated.")
    if len(enrolled_subjects) < 20:
        caveats.append(f"Only {len(enrolled_subjects)} enrolled subjects: collect at least 20-30 consenting "
                       "people for meaningful rates.")

    import onnxruntime as ort

    return EvaluationReport(
        dataset={"images": len(rows), "enroll_images": len(enroll), "probe_images": len(probes),
                 "enrolled_subjects": len(enrolled_subjects),
                 "impostor_subjects": len({r.subject_id for r in probes} - set(enrolled_subjects))},
        models={"detector": engine.detector.model_id, "embedder": engine.embedder.model_id},
        policy=asdict(engine.policy),
        enrollment={"templates": len(templates), "failed_to_enrol": len(fte), "failures": fte},
        overall=overall.as_dict(),
        by_condition=by_condition,
        threshold_sweep=sw,
        recommendation=recommend(sw, overall.impostor_attempts, target_fpir),
        latency_ms=latency_summary(records),
        environment={"python": platform.python_version(), "platform": platform.platform(),
                     "processor": platform.processor(), "onnxruntime_providers": ort.get_available_providers()},
        caveats=caveats,
    )


def _rank_face(engine: RecognitionEngine, img: np.ndarray, face) -> list[tuple[str, float]]:  # type: ignore[no-untyped-def]
    from recognition.align import align

    return engine.gallery.scores(engine.embedder.embed(align(img, face.detection.landmarks)))


def write_report(report: EvaluationReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / "evaluation_report.json", out_dir / "evaluation_report.md"
    jp.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    def pct(x: object) -> str:
        if isinstance(x, dict):
            r = x.get("rate")
            return "—" if r is None else f"{100 * float(r):.2f}% ({x['errors']}/{x['n']})"
        return str(x)

    o = report.overall
    lines = [
        "# Face recognition evaluation report",
        "",
        f"Models: detector `{report.models['detector']}`, embedder `{report.models['embedder']}`  ",
        f"Policy: threshold {report.policy['threshold']}, margin {report.policy['min_margin']}, "
        f"uncertain band {report.policy['uncertain_band']}",
        "",
        "## Dataset",
        *(f"- {k}: {v}" for k, v in report.dataset.items()),
        f"- failed to enrol: {report.enrollment['failed_to_enrol']}",
        "",
        "## Overall (at configured policy)",
        f"- FTA (no usable face): {pct(o['FTA'])}",
        f"- FNIR / false reject: {pct(o['FNIR_false_reject'])}",
        f"- Misidentification: {pct(o['MIR_misidentification'])}",
        f"- FPIR / false accept: {pct(o['FPIR_false_accept'])}",
        f"- Held as AMBIGUOUS (human review): {o['ambiguous_held_for_review']}",
        "",
        "## By condition",
    ]
    for c, groups in report.by_condition.items():
        lines += ["", f"### {c}", "", "| value | genuine | FTA | false reject | misidentified | impostor | false accept |",
                  "|---|---|---|---|---|---|---|"]
        for k, g in groups.items():
            lines.append(f"| {k} | {g['genuine_attempts']} | {pct(g['FTA'])} | {pct(g['FNIR_false_reject'])} | "
                         f"{pct(g['MIR_misidentification'])} | {g['impostor_attempts']} | {pct(g['FPIR_false_accept'])} |")
    rec = report.recommendation
    lines += ["", "## Threshold recommendation", "",
              f"Target FPIR {rec['target_FPIR']}: recommended threshold **{rec['recommended_threshold']}** "
              f"(impostor attempts {rec['impostor_attempts']}, needed {rec['impostor_attempts_needed_to_demonstrate_target']}, "
              f"sufficient: {rec['statistically_sufficient']})",
              "", "## Latency (ms)", "", "| stage | n | p50 | p95 | p99 | max |", "|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['n']} | {v['p50']} | {v['p95']} | {v['p99']} | {v['max']} |" for k, v in report.latency_ms.items()]
    lines += ["", "## Caveats", *(f"- {c}" for c in report.caveats), "",
              f"Environment: {report.environment}", ""]
    mp.write_text("\n".join(lines), encoding="utf-8")
    return jp, mp
