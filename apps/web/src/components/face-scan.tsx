"use client";

import { ArrowLeft, ArrowRight, Check, ChevronsLeft, ChevronsRight, Loader2, ScanFace, Smile } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { HOLD_FRAMES, SCAN_STEPS, checkFrame, enrolmentError, holdProgress, requiredSign, type ScanFrame } from "@/lib/face-scan";
import { MjpegParser } from "@/lib/mjpeg";
import type { FaceTemplate } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

const STEP_PAUSE_MS = 900;
const CLOSE_AFTER_MS = 1800;
const DEFAULT_LIMIT = 5;
/** With the live stream, the face is analysed this often (the video itself runs at ~15 fps). */
const ANALYZE_EVERY_MS = 220;
/** Failed stream connections in a row before falling back to single preview frames. */
const STREAM_MAX_FAILURES = 3;

type Phase = "ready" | "scanning" | "saving" | "done" | "failed";

const STEP_ICON = {
  front: ScanFace,
  left: ArrowLeft,
  right: ArrowRight,
  smile: Smile,
} as const;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Guided enrolment: live camera preview (mirrored like a selfie), one instruction at a time,
 * automatic capture when the pose is right and held for ~1 s. The server re-checks the pose on
 * the frame it enrols. Closes itself when every step is done.
 */
export function FaceScanModal({
  employeeId,
  employeeName,
  cameraId,
  cameraName,
  existing,
  onClose,
  onChanged,
}: {
  employeeId: string;
  employeeName: string;
  cameraId: string;
  cameraName: string;
  existing: FaceTemplate[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [frame, setFrame] = useState<ScanFrame | null>(null);
  const [preview, setPreview] = useState<string | null>(null); // fallback mode only
  const [streamLive, setStreamLive] = useState(false);
  const [notLive, setNotLive] = useState(false); // the camera is not monitored: no smooth video
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamLiveRef = useRef(false);
  const waitersRef = useRef<(() => void)[]>([]);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("ready");
  const [step, setStep] = useState(0);
  const [hold, setHold] = useState(0);
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [justDone, setJustDone] = useState(false);
  const [shots, setShots] = useState(0); // re-keys the capture flash
  const [replace, setReplace] = useState(existing.length + SCAN_STEPS.length > DEFAULT_LIMIT);

  // The polling loop reads the latest values through refs (it is started once).
  const phaseRef = useRef<Phase>("ready");
  const stepRef = useRef(0);
  const holdRef = useRef(0); // consecutive good frames
  const holdStartRef = useRef<number | null>(null);
  const signRef = useRef<1 | -1 | null>(null);
  const changedRef = useRef(false);
  const onChangedRef = useRef(onChanged);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onChangedRef.current = onChanged;
    onCloseRef.current = onClose;
  }, [onChanged, onClose]);

  const setPhaseBoth = (p: Phase) => {
    phaseRef.current = p;
    setPhase(p);
  };

  // The analysis loop waits for video frames (not timers: browsers clamp timers to 1 per second
  // in efficiency or background modes, which made the earlier preview look frozen).
  const wake = () => {
    const w = waitersRef.current;
    waitersRef.current = [];
    w.forEach((fn) => fn());
  };
  const nextVideoFrame = () => new Promise<void>((resolve) => waitersRef.current.push(resolve));

  // Smooth live video: MJPEG stream read with fetch (token in the header) and drawn on a canvas.
  useEffect(() => {
    let alive = true;
    const ctrl = new AbortController();
    const path = `/employees/${employeeId}/face-templates/scan-stream?camera_id=${encodeURIComponent(cameraId)}`;
    const run = async () => {
      let failures = 0;
      while (alive) {
        try {
          const res = await api.stream(path, ctrl.signal);
          const reader = res.body?.getReader();
          if (!reader) throw new Error("no stream body");
          const parser = new MjpegParser();
          for (;;) {
            const { value, done } = await reader.read();
            if (done || !alive) break;
            for (const jpeg of parser.push(value)) {
              const bitmap = await createImageBitmap(new Blob([jpeg], { type: "image/jpeg" }));
              const canvas = canvasRef.current;
              if (canvas) {
                if (canvas.width !== bitmap.width) canvas.width = bitmap.width;
                if (canvas.height !== bitmap.height) canvas.height = bitmap.height;
                canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
              }
              bitmap.close();
              failures = 0;
              if (!streamLiveRef.current) {
                streamLiveRef.current = true;
                setStreamLive(true);
              }
              wake();
            }
          }
        } catch (err) {
          if (!alive) return;
          if (err instanceof ApiError && err.code === "CAMERA_NOT_LIVE") {
            setNotLive(true); // retrying will not help; single frames are the only option
            failures = STREAM_MAX_FAILURES;
          }
          failures += 1;
        }
        // The server ends each stream after 2 minutes: reconnect. Repeated failures (camera not
        // monitored, service busy, old browser): fall back to single preview frames.
        if (failures >= STREAM_MAX_FAILURES) {
          streamLiveRef.current = false;
          setStreamLive(false);
          wake();
          return;
        }
      }
    };
    void run();
    return () => {
      alive = false;
      ctrl.abort();
      wake();
    };
  }, [employeeId, cameraId]);

  useEffect(() => {
    let alive = true;
    const url = `/employees/${employeeId}/face-templates`;

    const capture = async (latest: ScanFrame) => {
      const s = SCAN_STEPS[stepRef.current];
      setPhaseBoth("saving");
      try {
        const sign = requiredSign(s, signRef.current);
        const t = await api.request<FaceTemplate & { yaw_deg: number | null }>(`${url}/from-camera`, {
          method: "POST",
          json: { camera_id: cameraId, pose: s.pose, ...(sign !== undefined ? { yaw_sign: sign } : {}) },
        });
        changedRef.current = true;
        if (s.side === "first") {
          const yaw = t.yaw_deg ?? latest.face?.yaw_deg ?? 0;
          signRef.current = yaw > 0 ? 1 : -1;
        }
        if (!alive) return;
        setJustDone(true);
        setShots((n) => n + 1);
        await sleep(STEP_PAUSE_MS);
        if (!alive) return;
        setJustDone(false);
        holdRef.current = 0;
        holdStartRef.current = null;
        setHold(0);
        if (stepRef.current + 1 >= SCAN_STEPS.length) {
          setPhaseBoth("done");
          onChangedRef.current();
          await sleep(CLOSE_AFTER_MS);
          if (alive) onCloseRef.current();
          return;
        }
        stepRef.current += 1;
        setStep(stepRef.current);
        setPhaseBoth("scanning");
      } catch (err) {
        if (!alive) return;
        holdRef.current = 0;
        holdStartRef.current = null;
        setHold(0);
        if (err instanceof ApiError && (err.code === "POSE_MISMATCH" || err.code === "FACE_NOT_ACCEPTED")) {
          setMessage(err.code === "POSE_MISMATCH" ? "Qimirlab ketdingiz, yana bir bor" : enrolmentError(err));
          setPhaseBoth("scanning"); // try again on the next good frames
        } else {
          setFailure(enrolmentError(err));
          setPhaseBoth("failed");
        }
      }
    };

    const loop = async () => {
      while (alive) {
        try {
          const t0 = performance.now();
          const live = streamLiveRef.current;
          const f = await api.request<ScanFrame>(`${url}/scan-frame`, {
            method: "POST",
            json: { camera_id: cameraId, preview: !live },
          });
          if (!alive) return;
          setFrame(f);
          if (f.preview) setPreview(f.preview);
          setFrameError(null);
          if (phaseRef.current === "scanning") {
            const c = checkFrame(f, SCAN_STEPS[stepRef.current], signRef.current);
            holdRef.current = c.ok ? holdRef.current + 1 : 0;
            if (!c.ok) holdStartRef.current = null;
            else holdStartRef.current ??= performance.now();
            const progress = holdProgress(holdStartRef.current, performance.now());
            setHold(progress);
            setMessage(c.message);
            if (progress >= 1 && holdRef.current >= HOLD_FRAMES) await capture(f);
          }
          while (alive && streamLiveRef.current && performance.now() - t0 < ANALYZE_EVERY_MS) await nextVideoFrame();
        } catch (err) {
          if (!alive) return;
          setFrameError(enrolmentError(err));
          await sleep(1000);
        }
        // Fallback mode (no stream): the next request starts when this one ends, no timer.
      }
    };
    void loop();
    return () => {
      alive = false;
      if (changedRef.current) onChangedRef.current();
    };
  }, [employeeId, cameraId]);

  const start = async () => {
    setFailure(null);
    if (replace && existing.length > 0) {
      try {
        for (const t of existing) await api.request(`/employees/${employeeId}/face-templates/${t.id}`, { method: "DELETE" });
        changedRef.current = true;
      } catch (err) {
        setFailure(enrolmentError(err));
        return;
      }
    }
    stepRef.current = 0;
    signRef.current = null;
    holdRef.current = 0;
    holdStartRef.current = null;
    setStep(0);
    setHold(0);
    setMessage("");
    setPhaseBoth("scanning");
  };

  const current = SCAN_STEPS[step];
  const Icon = STEP_ICON[current.key];
  const active = phase === "scanning" || phase === "saving";
  const good = active && hold > 0;
  const box = frame?.face?.box;

  return (
    <Modal
      open
      size="lg"
      onClose={onClose}
      title="Yuzni skanerlash"
      description={`${employeeName} · ${cameraName}. Rasm saqlanmaydi, faqat shifrlangan namuna.`}
      footer={
        phase === "ready" || phase === "failed" ? (
          <>
            <Button variant="secondary" onClick={onClose}>Bekor qilish</Button>
            <Button icon={<ScanFace className="h-4 w-4" />} onClick={() => void start()} disabled={!frame}>
              {phase === "failed" ? "Qayta boshlash" : "Boshlash"}
            </Button>
          </>
        ) : phase === "done" ? null : (
          <Button variant="secondary" onClick={onClose}>Toʻxtatish</Button>
        )
      }
    >
      <div className="space-y-4">
        {/* Instruction (re-keyed so each new instruction slides in) */}
        <div
          key={`${phase === "done" ? "done" : phase === "ready" || phase === "failed" ? "ready" : step}`}
          className={cn(
            "flex animate-slide-up items-center gap-3 rounded-lg px-4 py-3 transition-colors duration-300",
            phase === "done" || good ? "bg-success-50 text-success-700" : "bg-navy-50 text-navy-800",
          )}
          aria-live="polite"
        >
          {phase === "done" ? (
            <Check className="scan-pop h-8 w-8 shrink-0" aria-hidden />
          ) : phase === "saving" ? (
            <Loader2 className="h-8 w-8 shrink-0 animate-spin" aria-hidden />
          ) : (
            <Icon
              className={cn(
                "h-8 w-8 shrink-0",
                active && current.key === "left" && "scan-nudge-left",
                active && current.key === "right" && "scan-nudge-right",
              )}
              aria-hidden
            />
          )}
          <div className="min-w-0">
            <p className="text-lg font-semibold">
              {phase === "done"
                ? "Tayyor! Barcha namunalar olindi"
                : phase === "ready" || phase === "failed"
                  ? "Kameraga qarab turing va “Boshlash”ni bosing"
                  : `${step + 1}/${SCAN_STEPS.length}. ${current.title}`}
            </p>
            {active ? <p className="text-sm">{justDone ? "Olindi ✓" : phase === "saving" ? "Namuna olinmoqda…" : message}</p> : null}
          </div>
        </div>

        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        {notLive ? (
          <Alert tone="warning" title="Bu kameraning monitoringi oʻchiq">
            Shuning uchun tasvir har bir necha soniyada bir yangilanadi. Silliq video uchun: Kameralar sahifasida “Monitoringni yoqish”ni bosing va oynani qayta oching.
          </Alert>
        ) : null}

        {/* Live preview, mirrored like a selfie (the face box is mirrored with it). */}
        <div className="relative overflow-hidden rounded-xl bg-navy-900">
          <div className={cn("relative", !streamLive && !preview && "hidden")} style={{ transform: "scaleX(-1)" }}>
            <canvas ref={canvasRef} aria-label="Kamera koʻrinishi" className={cn("block h-auto w-full", !streamLive && "hidden")} />
            {!streamLive && preview ? (
              // eslint-disable-next-line @next/next/no-img-element -- in-memory data: URL preview
              <img src={preview} alt="Kamera koʻrinishi" className="block h-auto w-full" />
            ) : null}
            {box && box.length === 4 && phase !== "done" ? (
              <div
                className="scan-frame"
                data-good={good || justDone ? "true" : "false"}
                style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` }}
              >
                {(["tl", "tr", "bl", "br"] as const).map((p) => <span key={p} className="scan-corner" data-pos={p} />)}
                {active && !justDone ? <span className="scan-line" /> : null}
              </div>
            ) : null}
          </div>

          {/* Overlays that must not be mirrored */}
          {(streamLive || preview) && !box && phase !== "done" ? (
            <div className="pointer-events-none absolute inset-0">
              <div className="scan-guide" />
              <p className="absolute inset-x-0 bottom-4 text-center text-sm font-medium text-white drop-shadow">
                Yuzingizni oval ichiga keltiring
              </p>
            </div>
          ) : null}
          {active && !justDone && (current.key === "left" || current.key === "right") ? (
            <div
              className={cn(
                "pointer-events-none absolute inset-y-0 flex items-center px-2 text-white drop-shadow-lg",
                current.key === "left" ? "left-0" : "right-0",
              )}
              aria-hidden
            >
              {current.key === "left" ? (
                <ChevronsLeft className="scan-nudge-left h-14 w-14" />
              ) : (
                <ChevronsRight className="scan-nudge-right h-14 w-14" />
              )}
            </div>
          ) : null}
          {active ? <HoldRing value={phase === "saving" ? 1 : hold} /> : null}
          {shots > 0 ? <div key={shots} className="scan-flash" /> : null}
          {justDone ? (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <span className="scan-pop flex h-20 w-20 items-center justify-center rounded-full bg-success-600/90 text-white shadow-lg">
                <Check className="h-11 w-11" strokeWidth={3} aria-hidden />
              </span>
            </div>
          ) : null}
          {phase === "done" ? (
            <div className="absolute inset-0 flex animate-fade-in items-center justify-center bg-navy-950/60">
              <span className="relative flex h-28 w-28 items-center justify-center">
                <span className="scan-ripple" />
                <span className="scan-ripple" data-delay="1" />
                <span className="scan-pop flex h-28 w-28 items-center justify-center rounded-full bg-success-600 shadow-xl">
                  <svg viewBox="0 0 24 24" className="h-16 w-16" fill="none" stroke="white" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path className="scan-draw" d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                </span>
              </span>
            </div>
          ) : null}

          {!streamLive && !preview ? (
            <div className="flex aspect-[4/3] items-center justify-center text-sm text-navy-200">
              {frameError ?? (
                <span className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Kamera ulanmoqda…
                </span>
              )}
            </div>
          ) : null}
          {(streamLive || preview) && frameError ? (
            <p className="absolute inset-x-0 bottom-0 bg-danger-600/90 px-3 py-1 text-xs text-white">{frameError}</p>
          ) : null}
        </div>

        {/* Steps */}
        <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {SCAN_STEPS.map((s, i) => {
            const done = phase === "done" || (active && i < step) || (justDone && i === step);
            const now = active && i === step && !justDone;
            const StepIcon = STEP_ICON[s.key];
            return (
              <li
                key={s.key}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition-colors duration-300",
                  done ? "border-success-600/30 bg-success-50 text-success-700" : now ? "scan-current border-brand-200 bg-brand-50 text-navy-900" : "border-line text-muted",
                )}
              >
                {done ? (
                  <Check key="done" className="scan-pop h-4 w-4 shrink-0" aria-hidden />
                ) : (
                  <StepIcon className="h-4 w-4 shrink-0" aria-hidden />
                )}
                <span>{s.title}</span>
              </li>
            );
          })}
        </ol>

        {phase === "ready" && existing.length > 0 ? (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-0.5" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            <span>
              Eski {existing.length} ta namunani oʻchirib, yangidan olish
              <span className="block text-xs text-muted">Bir xodimga koʻpi bilan {DEFAULT_LIMIT} ta namuna saqlanadi.</span>
            </span>
          </label>
        ) : null}
      </div>
    </Modal>
  );
}

/** Circular progress of holding the pose (top-right of the preview). */
function HoldRing({ value }: { value: number }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  return (
    <div className="pointer-events-none absolute right-3 top-3 h-12 w-12 drop-shadow" aria-hidden>
      <svg viewBox="0 0 44 44" className="h-12 w-12 -rotate-90">
        <circle cx="22" cy="22" r={r} fill="rgb(12 21 42 / 0.45)" stroke="rgb(255 255 255 / 0.25)" strokeWidth="4" />
        <circle
          cx="22"
          cy="22"
          r={r}
          fill="none"
          stroke="#10b981"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value)}
          style={{ transition: "stroke-dashoffset 150ms linear" }}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold text-white">
        {Math.round(value * 100)}%
      </span>
    </div>
  );
}
