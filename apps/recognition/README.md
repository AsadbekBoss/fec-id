# Recognition service (Phase 9: evaluation, NOT connected to attendance)

This is an independent Python service for face detection, alignment, quality assessment,
embedding, and conservative 1:N matching against **authorised (consented, enrolled)
employees**. It creates no attendance events. The connection to attendance is blocked until the
evaluation in [EVALUATION_PROTOCOL.md](EVALUATION_PROTOCOL.md) has been run and accepted.

## Pipeline

`decode → detect (YuNet) → quality pre-check → align (5-point similarity → 112×112) → quality post-check → embed (SFace, 128-d) → match → decision`

| Decision | When | Identity returned? |
|---|---|---|
| `MATCHED` | top-1 ≥ threshold + uncertain band **and** margin to the next employee ≥ `MIN_MARGIN` | yes |
| `AMBIGUOUS` | similar to someone, but inside the uncertain band or with too small a margin | **no**: held for human review |
| `UNKNOWN` | below threshold, or the gallery is empty | no |
| `LOW_QUALITY` | failed the quality gate: too small, blurry, too dark or bright, head turned or tilted, low detector score | no, and never compared |

## Models and licences

| Model | Use | Licence | Commercial | Default |
|---|---|---|---|---|
| YuNet 2023mar (OpenCV Zoo) | detection + 5 landmarks | MIT | ✅ | ✅ |
| SFace 2021dec (OpenCV Zoo) | 128-d embedding | Apache-2.0 | ✅ | ✅ |
| InsightFace SCRFD-10G | detection | InsightFace: non-commercial research only | ❌ | research only |
| InsightFace ArcFace w600k_r50 | 512-d embedding | InsightFace: non-commercial research only | ❌ | research only |

`models/manifest.yaml` is the gate. A model loads only if it is listed, its SHA-256 matches, and
it is commercially usable. The InsightFace weights load only with `ALLOW_RESEARCH_MODELS=true`
outside production. They are refused in production unless a commercial licence is obtained and
recorded (decision D2). Weights are git-ignored and are never committed.

## Security

- Templates are stored only AES-256-GCM encrypted (the same keyring and format as the Core API;
  interoperability is tested with a ciphertext produced by `apps/api`). The AAD binds each
  ciphertext to its template, subject and model.
- Every enrolment requires a consent reference.
- `/internal/*`, `/stats` and `/metrics` require `Authorization: Bearer $SERVICE_TOKEN` (constant-time check;
  the service refuses to start with a weak token). The service is meant for the Docker-internal
  network only.
- Responses and logs never contain embeddings or images. An employee id appears only for `MATCHED`.

## Run

```bash
npm run rec:setup        # create .venv and install dependencies
cp apps/recognition/.env.example apps/recognition/.env   # set SERVICE_TOKEN and DATA_ENCRYPTION_KEYS
npm run rec:download     # YuNet + SFace (sha256 verified)
npm run rec:check        # licence + checksum gate
npm run rec:dev          # http://127.0.0.1:8100  (/health, /ready, /docs)
npm run test:rec         # unit + integration tests
npm run rec:bench        # stage latency on this machine (synthetic frames: speed, not accuracy)
npm run rec:enroll -- --dataset DIR --gallery DIR/gallery.json
npm run rec:eval   -- --dataset DIR --out DIR/report
```

Docker (opt-in): `docker compose --profile recognition up --build recognition`. This needs
`RECOGNITION_SERVICE_TOKEN` in the root `.env`.

## Configuration (env)

| Variable | Default | Meaning |
|---|---|---|
| `DETECTOR` / `EMBEDDER` | `yunet` / `sface` | model names from the manifest |
| `MATCH_THRESHOLD` | manifest reference (SFace 0.363) | cosine threshold. **Calibrate with the evaluation.** |
| `MIN_MARGIN` | 0.05 | top-1 minus the best other employee |
| `UNCERTAIN_BAND` | 0.03 | scores in [threshold, threshold + band) are AMBIGUOUS |
| `MIN_FACE_WIDTH_PX` / `MIN_INTEROCULAR_PX` | 80 / 28 | resolution gate |
| `MIN_DETECTION_SCORE` | 0.85 | detector confidence gate |
| `MIN_BLUR_VARIANCE` | 40 | Laplacian variance of the aligned crop |
| `MIN_BRIGHTNESS` / `MAX_BRIGHTNESS` | 40 / 215 | exposure gate |
| `MAX_ABS_YAW_DEG` / `MAX_ABS_ROLL_DEG` | 30 / 20 | pose gate |
| `MAX_UPLOAD_BYTES` / `MAX_IMAGE_SIDE` | 8 MB / 1920 | input limits |
| `GALLERY_PATH` | — | encrypted gallery file loaded at start (evaluation) |

## Internal API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | liveness |
| GET | `/ready` | — | models loaded, gallery size, `attendance_integration: disabled` |
| GET | `/stats` | token | counts, decisions, quality rejections, latency p50/p95/p99 |
| GET | `/metrics` | token | Prometheus text format |
| POST | `/internal/v1/quality` | token | multipart `file`: faces + quality report, **no identification** |
| POST | `/internal/v1/recognize` | token | multipart `file`: faces + quality + decision (+ employee id only if MATCHED) |

## Measured on this development machine (CPU, ONNX/OpenCV DNN, synthetic frames)

| Stage | p50 | p95 |
|---|---|---|
| YuNet detection, full 1920×1080 frame | ~214 ms | ~477 ms |
| Alignment | ~0.6 ms | ~1.9 ms |
| SFace embedding, per face | ~27 ms | ~70 ms |

These numbers are speed only. Accuracy (FAR/FRR) needs a consented dataset; see the protocol.
