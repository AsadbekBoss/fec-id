"""Recognition service CLI.

    python -m recognition.cli check-models          # licence + checksum gate for configured models
    python -m recognition.cli download-models       # commercially usable models with known URLs
    python -m recognition.cli bench [--frames 50]   # per-stage latency on this machine (synthetic)
    python -m recognition.cli enroll  --dataset DIR --gallery FILE      # encrypted gallery
    python -m recognition.cli evaluate --dataset DIR --out DIR [--target-fpir 0.001]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from recognition import evaluate, gallery_store, registry
from recognition.align import align
from recognition.config import get_settings
from recognition.crypto import Keyring
from recognition.engine import RecognitionEngine, decode_image


def _engine() -> RecognitionEngine:
    try:
        return RecognitionEngine.from_settings(get_settings())
    except registry.ModelRefused as e:
        raise SystemExit(f"Model refused: {e}") from None


def cmd_check(_: argparse.Namespace) -> None:
    s = get_settings()
    manifest = registry.load_manifest(s.manifest_path)
    for name in (s.detector, s.embedder):
        try:
            spec, path = registry.resolve(name, manifest, s.models_dir, app_env=s.app_env,
                                          allow_research=s.allow_research_models)
            print(f"OK   {spec.model_id:24} {spec.license:12} commercial_use={spec.commercial_use}  {path.name}")
        except registry.ModelRefused as e:
            print(f"REFUSED {name}: {e}")
            raise SystemExit(1) from None


def cmd_download(_: argparse.Namespace) -> None:
    s = get_settings()
    for spec in registry.load_manifest(s.manifest_path).values():
        if spec.commercial_use == "forbidden" or not spec.url:
            print(f"skip {spec.name}: {spec.license} (manual, licensed copy only)")
            continue
        path = registry.download(spec, s.models_dir)
        print(f"ok   {spec.name} -> {path} (sha256 verified)")


def cmd_bench(a: argparse.Namespace) -> None:
    """Latency of each stage on synthetic input. No faces are needed, so it measures speed,
    NOT accuracy. Detection runs on full HD frames, embedding on aligned crops."""
    eng = _engine()
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    crop_lm = np.array([[38.3, 51.7], [73.5, 51.5], [56.0, 71.7], [41.5, 92.4], [70.7, 92.2]], np.float32) * 3
    big = rng.integers(0, 255, (400, 400, 3), dtype=np.uint8)
    eng.detector.detect(frame)  # warm-up
    det, emb, ali = [], [], []
    for _ in range(a.frames):
        t = time.perf_counter()
        eng.detector.detect(frame)
        det.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        crop = align(big, crop_lm)
        ali.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        eng.embedder.embed(crop)
        emb.append((time.perf_counter() - t) * 1000)
    def q(v: list[float]) -> dict[str, float]:
        return {"p50": round(float(np.percentile(v, 50)), 2), "p95": round(float(np.percentile(v, 95)), 2),
                "max": round(max(v), 2)}

    print(json.dumps({"frames": a.frames, "frame": "1920x1080", "detect_ms": q(det), "align_ms": q(ali),
                      "embed_ms_per_face": q(emb), "models": [eng.detector.model_id, eng.embedder.model_id]}, indent=2))


def cmd_enroll(a: argparse.Namespace) -> None:
    s = get_settings()
    eng = _engine()
    keyring = Keyring.parse(s.data_encryption_keys.get_secret_value(), s.data_encryption_active_key_id)
    rows = [r for r in evaluate.read_manifest(Path(a.dataset)) if r.split == "enroll"]
    entries, failed = [], 0
    for r in rows:
        vec, face, err = eng.embed_best_face(decode_image((Path(a.dataset) / r.image).read_bytes(), s.max_image_side))
        if vec is None or face is None:
            failed += 1
            print(f"skip {r.image}: {err}")
            continue
        entries.append(gallery_store.make_entry(vec, r.subject_id, eng.embedder.model_id, r.consent_id,
                                                face.detection.score, keyring))
    gallery_store.save(Path(a.gallery), entries)
    print(f"Encrypted gallery written: {a.gallery} ({len(entries)} templates, {failed} failed)")


def cmd_evaluate(a: argparse.Namespace) -> None:
    report = evaluate.run(_engine(), Path(a.dataset), a.target_fpir)
    jp, mp = evaluate.write_report(report, Path(a.out))
    print(f"Report: {mp}\nData:   {jp}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m recognition.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-models").set_defaults(fn=cmd_check)
    sub.add_parser("download-models").set_defaults(fn=cmd_download)
    b = sub.add_parser("bench")
    b.add_argument("--frames", type=int, default=50)
    b.set_defaults(fn=cmd_bench)
    e = sub.add_parser("enroll")
    e.add_argument("--dataset", required=True)
    e.add_argument("--gallery", required=True)
    e.set_defaults(fn=cmd_enroll)
    v = sub.add_parser("evaluate")
    v.add_argument("--dataset", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--target-fpir", type=float, default=0.001)
    v.set_defaults(fn=cmd_evaluate)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except evaluate.DatasetError as e:
        print(f"Dataset error: {e}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
