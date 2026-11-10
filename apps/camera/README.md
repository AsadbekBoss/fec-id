# Camera service (Phase 10)

This independent service connects to cameras, monitors them, reconnects automatically, reports
status to the Core API, and tests whether a stream is suitable for face recognition. It does
**not** record video and does **not** create attendance events.

```
Core API ──GET /internal/v1/cameras (config, decrypted credentials)──▶ camera service
Core API ◀─POST /internal/v1/cameras/status (state, fps, errors)────── camera service
Core API ──POST /internal/v1/probe | snapshot | quality-test─────────▶ camera service
camera service ──POST /internal/v1/quality (frames)──▶ recognition service (quality test only)
```

## Providers (replaceable)

| Provider | Use | Notes |
|---|---|---|
| `RTSP` | IP cameras | OpenCV/FFmpeg over TCP (UDP optional), open/read timeouts, TCP pre-check (`UNREACHABLE` vs `OPEN_FAILED`) |
| `USB` | Webcam on this host | Index `0`–`99` or `/dev/videoN` (DirectShow on Windows, V4L2 on Linux) |
| `TEST` | Development without hardware | Synthetic pattern, or replay of an image folder or video from `TEST_MEDIA_DIR`. `simulate`: `offline`, `disconnect`, `flaky`, `freeze` |

All providers implement `FrameSource` (`open / grab / retrieve / close`). To replace or add a
provider, call `camera.providers.registry.register("NAME", factory)`.

## Monitoring and reconnection

- One worker thread per enabled camera. It keeps the stream drained (`grab`) and decodes `sample_fps` frames (`retrieve`).
- States: `CONNECTING → ONLINE / DEGRADED → OFFLINE → CONNECTING …`
  - `DEGRADED`: at least 3 read failures in the last 10 s, or the measured fps is below half the camera's reported fps.
  - `OFFLINE`: the stream could not be opened, or no frame arrived for `STALL_TIMEOUT_SECONDS` (10 s).
  - Reconnect uses exponential backoff from 1 s to 60 s, with ±20 % jitter.
- Status goes to the Core API every `SYNC_INTERVAL_SECONDS` (10 s). The Core API shows a camera as "status unknown" if no report has arrived for 60 s.
- If the Core API is down, workers keep running with the last known configuration.

## Privacy and security

- **No recording.** Each worker keeps only the latest sampled frame in memory. Snapshots are encoded on request and never written to disk.
- RTSP credentials come only from the Core API's internal endpoint. They are URL-encoded into the capture URL in memory, never logged or returned, and error messages are redacted. FFmpeg logging is silenced.
- Every `/internal` route requires `Authorization: Bearer $SERVICE_TOKEN` (constant-time check, at least 24 characters). The service is meant for the internal network only.

## Stream quality test

The test runs for 2–20 s and checks:
- resolution of at least 640×480;
- frame rate;
- frozen frames;
- exposure;
- sharpness;
- faces, if `RECOGNITION_URL` is set: up to 5 frames go to the recognition service's quality endpoint, and the face width is compared with the camera's `min_face_px`.

The verdict is one of:
- `FAILED`: the stream or the faces fail.
- `INCONCLUSIVE`: the stream is fine, but no face was assessed. Re-run with a person standing at the planned distance.
- `PASSED`: the stream is fine and enough faces pass the quality gate.

The Core API allows face recognition on a camera only after `PASSED`. A pass does not prove recognition accuracy; that needs `apps/recognition/EVALUATION_PROTOCOL.md`.

## Run

```bash
npm run cam:setup                                       # .venv + dependencies
cp apps/camera/.env.example apps/camera/.env            # set SERVICE_TOKEN (same as CAMERA_SERVICE_TOKEN in apps/api/.env)
# apps/api/.env: CAMERA_SERVICE_URL=http://127.0.0.1:8200 and CAMERA_SERVICE_TOKEN=<same token>
npm run db:migrate                                      # migration 0005 (camera providers)
npm run dev:api                                         # Core API
npm run cam:dev                                         # camera service on :8200
npm run rec:dev                                         # optional: faces in the quality test
npm run test:cam                                        # tests, no hardware needed
```

Then open **Kameralar** in the web app. Add a "Test kamera", enable monitoring, and try the connection test, snapshot and quality test.

Docker (opt-in): `docker compose --profile cameras up --build camera`. This needs `CAMERA_SERVICE_TOKEN` in the root `.env`. USB cameras need a device mapping.
