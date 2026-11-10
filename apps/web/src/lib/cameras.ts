/** Camera form/status logic, kept pure so it can be unit-tested. */
import type { BadgeTone } from "@/components/ui/badge";
import { CAMERA_STATUS } from "@/lib/labels";
import type { Camera, CameraDirection, CameraProvider, TestSimulate } from "@/lib/types";

export type CameraForm = {
  name: string;
  location: string;
  provider: CameraProvider;
  direction: CameraDirection;
  host: string;
  rtsp_port: string;
  main_stream_path: string;
  sub_stream_path: string;
  transport: "tcp" | "udp";
  device: string;
  /** Write-only. Empty = keep the stored credentials (edit) / no credentials (create). */
  username: string;
  password: string;
  clear_credentials: boolean;
  sample_fps: string;
  min_face_px: string;
  test_source: "pattern" | "media";
  test_media: string;
  test_width: string;
  test_height: string;
  test_fps: string;
  test_simulate: TestSimulate;
};

export function emptyForm(): CameraForm {
  return {
    name: "", location: "", provider: "TEST", direction: "IN", host: "", rtsp_port: "554",
    main_stream_path: "", sub_stream_path: "", transport: "tcp", device: "0", username: "", password: "",
    clear_credentials: false, sample_fps: "5", min_face_px: "80", test_source: "pattern", test_media: "",
    test_width: "1280", test_height: "720", test_fps: "15", test_simulate: "none",
  };
}

export function formFromCamera(c: Camera): CameraForm {
  const t = c.settings.test ?? {};
  return {
    ...emptyForm(),
    name: c.name,
    location: c.location ?? "",
    provider: c.provider,
    direction: c.direction,
    host: c.host ?? "",
    rtsp_port: String(c.rtsp_port),
    main_stream_path: c.main_stream_path ?? "",
    sub_stream_path: c.sub_stream_path ?? "",
    transport: c.settings.transport ?? "tcp",
    device: c.device ?? "0",
    sample_fps: String(c.settings.sample_fps ?? 5),
    min_face_px: String(c.settings.min_face_px ?? 80),
    test_source: t.source ?? "pattern",
    test_media: t.media ?? "",
    test_width: String(t.width ?? 1280),
    test_height: String(t.height ?? 720),
    test_fps: String(t.fps ?? 15),
    test_simulate: t.simulate ?? "none",
  };
}

const HOST = /^[A-Za-z0-9.:-]{1,255}$/;
const DEVICE = /^([0-9]{1,2}|\/dev\/video[0-9]{1,2})$/;
const MEDIA = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

function intIn(v: string, lo: number, hi: number): boolean {
  if (!/^\d+$/.test(v)) return false;
  const n = Number(v);
  return n >= lo && n <= hi;
}

/** Client-side mirror of the server rules (the server re-validates everything). */
export function validateCameraForm(f: CameraForm): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.name.trim()) e.name = "Nomini kiriting.";
  if (!intIn(f.sample_fps, 1, 30)) e.sample_fps = "1–30";
  if (!intIn(f.min_face_px, 40, 400)) e.min_face_px = "40–400 piksel";
  if (f.provider === "RTSP") {
    if (!HOST.test(f.host.trim())) e.host = "IP manzil yoki xost nomi.";
    if (!intIn(f.rtsp_port, 1, 65535)) e.rtsp_port = "1–65535";
    for (const k of ["main_stream_path", "sub_stream_path"] as const) {
      const p = f[k].trim();
      if (k === "main_stream_path" && !p) e[k] = "Oqim yoʻlini kiriting (masalan /Streams/Channels/101).";
      else if (p && (!p.startsWith("/") || p.includes("@") || p.includes("://") || /\s/.test(p)))
        e[k] = "“/” bilan boshlansin; login/parol va boʻsh joy boʻlmasin.";
    }
    const hasUser = f.username !== "";
    const hasPass = f.password !== "";
    if (hasUser !== hasPass) e[hasUser ? "password" : "username"] = "Login va parol birga kiritiladi.";
  } else if (f.provider === "USB") {
    if (!DEVICE.test(f.device.trim())) e.device = "Raqam (0, 1, …) yoki /dev/videoN.";
  } else {
    if (f.test_source === "media" && (!MEDIA.test(f.test_media) || f.test_media.includes("..")))
      e.test_media = "TEST_MEDIA_DIR ichidagi fayl yoki papka nomi.";
    if (!intIn(f.test_width, 160, 3840)) e.test_width = "160–3840";
    if (!intIn(f.test_height, 120, 2160)) e.test_height = "120–2160";
    if (!intIn(f.test_fps, 1, 60)) e.test_fps = "1–60";
  }
  return e;
}

/** Full desired state as an API body. Credentials are included only when typed in. */
export function cameraBody(f: CameraForm): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    sample_fps: Number(f.sample_fps),
    min_face_px: Number(f.min_face_px),
    transport: f.transport,
    test:
      f.provider === "TEST"
        ? {
            source: f.test_source,
            media: f.test_source === "media" ? f.test_media : null,
            width: Number(f.test_width),
            height: Number(f.test_height),
            fps: Number(f.test_fps),
            simulate: f.test_simulate,
          }
        : null,
  };
  const body: Record<string, unknown> = {
    name: f.name.trim(),
    location: f.location.trim() || null,
    provider: f.provider,
    direction: f.direction,
    settings,
  };
  if (f.provider === "RTSP") {
    Object.assign(body, {
      host: f.host.trim(),
      rtsp_port: Number(f.rtsp_port),
      main_stream_path: f.main_stream_path.trim(),
      sub_stream_path: f.sub_stream_path.trim() || null,
    });
    if (f.username !== "" && f.password !== "") Object.assign(body, { username: f.username, password: f.password });
  } else if (f.provider === "USB") {
    body.device = f.device.trim();
  }
  return body;
}

/** PATCH body: only changed fields (credentials only when typed in or explicitly cleared). */
export function cameraPatch(f: CameraForm, c: Camera): Record<string, unknown> {
  const full = cameraBody(f);
  const before = cameraBody(formFromCamera(c));
  const patch: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(full)) {
    if (k === "username" || k === "password") continue;
    if (JSON.stringify(v) !== JSON.stringify(before[k])) patch[k] = v;
  }
  if ("username" in full) Object.assign(patch, { username: full.username, password: full.password });
  else if (f.clear_credentials && c.has_credentials) patch.clear_credentials = true;
  return patch;
}

/** What to show for status: a disabled camera, or a stale report, is not shown as live. */
export function statusView(c: Camera): { label: string; tone: BadgeTone; hint?: string } {
  if (!c.is_enabled) return c.status === "DRAFT" ? CAMERA_STATUS.DRAFT : CAMERA_STATUS.DISABLED;
  if (c.monitoring?.stale) {
    return {
      label: "Holat nomaʼlum",
      tone: "warning",
      hint: c.monitoring.reported_at
        ? "Kamera xizmatidan yangi hisobot kelmayapti."
        : "Kamera xizmati hali hisobot yubormadi (ishga tushirilganmi?).",
    };
  }
  return CAMERA_STATUS[c.status];
}

/** Recognition can be switched on only after a PASSED quality test (server-enforced too). */
export function canEnableRecognition(c: Camera): boolean {
  return c.quality_status === "PASSED";
}
