# Face recognition evaluation protocol

**Status: required before any connection to the attendance system.**
The recognition service stays disconnected from attendance until a report produced with this
protocol has been reviewed and accepted by the owner.

> **No guarantee at 3–5 m.** Whether a face can be recognised at 3 m or 5 m depends on how many
> pixels the face covers, and that depends on the camera, lens, mounting height, focus, lighting
> and motion. This protocol *measures* the result for one specific installation. It does not
> promise one. Faces that are too small are rejected as `LOW_QUALITY` by design. The system
> never guesses who they are.

## 1. Consent and data handling

- Use **consenting adult volunteers only**. Each person signs a written consent for *evaluation
  capture*. That consent is separate from production enrolment consent (DEVELOPMENT_RULES §2).
- Each person gets a pseudonymous `subject_id` (e.g. `S017`). Names never appear in the dataset.
  The mapping from name to `subject_id` stays with the consent forms and is not stored in the dataset.
- Every row of `manifest.csv` must have a `consent_id`. The harness refuses rows without one.
- Store the dataset on an encrypted disk, outside the repository (`data/` is git-ignored anyway).
  Delete it when the evaluation ends, unless the consent states a retention period.
- Never commit images, galleries (`gallery.json`) or reports that contain subject ids.

## 2. Participants

| Group | Count | Role |
|---|---|---|
| Enrolled subjects | **≥ 20–30** (more is better) | Images in `enroll` **and** `probe` |
| Impostors | **≥ 10** different people | `probe` images only (never enrolled) |

- Include variety: age, skin tone, glasses, beards, head coverings worn at work.
- The number of **impostor attempts** limits how small a false-accept rate you can demonstrate.
  With zero false accepts in N attempts, the 95 % upper bound is about 3 / N (rule of three).
  To show FPIR ≤ 0.1 % you need **≥ 3 000** impostor attempts. The report computes this and states
  whether the sample is sufficient.

## 3. Condition matrix

Capture probes with the **real camera at its real mounting position**. Use the values below
exactly, because the report groups results by these labels:

| Column | Values |
|---|---|
| `lighting` | `good` (even office light), `low` (dim, evening), `backlit` (window or door behind the person) |
| `angle` | `frontal`, `yaw15` (head turned ~15°), `yaw30`, `pitch` (looking down at a phone, ~20°) |
| `distance_m` | `1`, `3`, `5` (measured on the floor from the point below the camera) |

Minimum for each enrolled subject: every `distance_m` × `angle` combination under `good` light,
plus `frontal` at every distance under `low` and `backlit` light. That is 12 + 6 = 18 probes per
subject. Use the same matrix for impostors.

**Enrolment images** (`split=enroll`): 3–5 per subject, frontal, good light, about 1 m, neutral
expression, one face in the frame. Enrolment failures are reported (`failed_to_enrol`).

## 4. Capture instructions

1. Mount the camera where it will really be installed (height, tilt, lens). Do not move it
   between sessions.
2. Use the stream resolution the production system will use (e.g. 1920×1080). Save stills from
   the stream or high-quality JPEG snapshots, not phone photos.
3. People walk or stand naturally. Do not coach them to look at the lens, except for enrolment.
4. Use one image per probe, with one person in the frame. If several faces are present, the
   harness evaluates the largest one.
5. Write `manifest.csv` (UTF-8):

```csv
image,subject_id,split,lighting,angle,distance_m,consent_id
enroll/S001_a.jpg,S001,enroll,good,frontal,1,CONSENT-2026-001
probe/S001_good_frontal_3.jpg,S001,probe,good,frontal,3,CONSENT-2026-001
probe/X104_good_frontal_5.jpg,X104,probe,good,frontal,5,CONSENT-2026-104
```

## 5. Expected face size: estimate before capturing

Face width in pixels ≈ `image_width × 0.16 m / (2 × distance × tan(HFOV / 2))`.
The default quality gate requires **≥ 80 px** face width (`MIN_FACE_WIDTH_PX`) and **≥ 28 px**
between the eyes.

| Camera (1920 px wide) | Approx. HFOV | 1 m | 3 m | 5 m |
|---|---|---|---|---|
| 2.8 mm lens | ~100° | ~129 px | ~43 px ✗ | ~26 px ✗ |
| 4 mm lens | ~80° | ~183 px | ~61 px ✗ | ~37 px ✗ |
| 6 mm lens | ~54° | ~301 px | ~100 px ✓ | ~60 px ✗ |
| 12 mm lens | ~28° | (too close) | ~206 px ✓ | ~123 px ✓ |
| 4K (3840 px), 2.8 mm | ~100° | ~258 px | ~86 px ✓ | ~52 px ✗ |

*These are estimates only. Real values depend on the sensor size, distortion and focus. Measure
the face size in your own images: the `/internal/v1/quality` endpoint reports `face_width_px`.*
A wide-angle 1080p camera **will not** produce usable faces at 3–5 m, and the system will
correctly report them as `face_too_small` instead of guessing. Lowering `MIN_FACE_WIDTH_PX` to
"make it work" raises the misidentification risk and must be justified by measured results.

## 6. Running the evaluation

```bash
npm run rec:setup                 # once
npm run rec:check                 # licence + checksum gate
npm run rec:eval -- --dataset D:/secure/eval-2026-11 --out D:/secure/eval-2026-11/report --target-fpir 0.001
npm run rec:bench                 # latency of each stage on this machine
```

The report (`evaluation_report.md` and `.json`) contains:

| Metric | Meaning |
|---|---|
| FTA | failure to acquire: no face found, or the quality gate failed |
| FNIR (false reject) | genuine probe not confidently identified as its own subject (includes FTA and AMBIGUOUS) |
| MIR (misidentification) | genuine probe MATCHED to a **different** person. **Must be 0.** |
| FPIR (false accept) | impostor MATCHED to any enrolled person |
| Held as AMBIGUOUS | observations sent to human review instead of being identified |
| Latency | p50/p95/p99 for each stage (detect, align+quality, embed, total) |

The report also contains a breakdown by lighting, angle and distance; a threshold sweep from
0.20 to 0.80; and a recommended threshold for the target FPIR (lowest FNIR with zero
misidentifications). It states whether the sample is large enough to support that target.

## 7. Acceptance (owner decision)

Fill in before connecting attendance (PHASE 9 gate):

| Criterion | Target (proposal) | Measured | OK? |
|---|---|---|---|
| Misidentification at chosen threshold | 0 | | |
| FPIR (with sufficient impostor attempts) | ≤ 0.1 % | | |
| FNIR, good light, frontal, at the planned distance | ≤ 5 % | | |
| p95 latency per frame on the target hardware | ≤ 500 ms | | |
| Consent records complete for every subject | yes | | |

If the 3 m or 5 m rows fail, the options are a longer lens, a higher-resolution camera, mounting
the camera closer to the entrance, or a walk-up point (1 m). Lowering the thresholds is not an option.
