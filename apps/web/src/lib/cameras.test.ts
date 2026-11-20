import { describe, expect, it } from "vitest";
import { cameraBody, cameraPatch, canEnableRecognition, emptyForm, formFromCamera, statusView, validateCameraForm, type CameraForm } from "@/lib/cameras";
import type { Camera } from "@/lib/types";

function camera(over: Partial<Camera> = {}): Camera {
  return {
    id: "c1", branch_id: "b1", name: "Main entrance", location: null, provider: "RTSP", vendor: "GENERIC", model: null,
    host: "10.0.0.5", rtsp_port: 554, onvif_port: null, main_stream_path: "/Streams/101", sub_stream_path: null,
    device: null, has_credentials: true, stream_url_redacted: "rtsp://***@10.0.0.5:554/Streams/101",
    direction: "IN", recognition_enabled: false, is_enabled: true, status: "ACTIVE",
    monitoring: { reported_at: "2026-11-08T10:00:00Z", stale: false, runtime: null }, last_seen_at: null,
    last_error: null, quality_status: "UNTESTED", quality_tested_at: null, quality_report: null, roi: null,
    settings: { sample_fps: 5, min_face_px: 80, transport: "tcp", test: null }, department_ids: [], config_version: 3,
    created_at: "2026-11-08T09:00:00Z", updated_at: "2026-11-08T09:00:00Z", ...over,
  };
}

const rtspForm = (over: Partial<CameraForm> = {}): CameraForm => ({
  ...emptyForm(), name: "Gate", provider: "RTSP", host: "192.168.1.64", main_stream_path: "/Streams/Channels/101", ...over,
});

describe("camera form validation", () => {
  it("accepts a valid RTSP, USB and TEST camera", () => {
    expect(validateCameraForm(rtspForm())).toEqual({});
    expect(validateCameraForm({ ...emptyForm(), name: "Webcam", provider: "USB", device: "0" })).toEqual({});
    expect(validateCameraForm({ ...emptyForm(), name: "Sim" })).toEqual({});
  });

  it("rejects credentials in the stream path and half credentials", () => {
    const e = validateCameraForm(rtspForm({ main_stream_path: "rtsp://admin:pw@cam/101", username: "admin" }));
    expect(e.main_stream_path).toBeTruthy();
    expect(e.password).toBeTruthy();
  });

  it("validates host, port, device and media name", () => {
    expect(validateCameraForm(rtspForm({ host: "bad host;rm", rtsp_port: "70000" }))).toMatchObject({ host: expect.any(String), rtsp_port: "1–65535" });
    expect(validateCameraForm({ ...emptyForm(), name: "x", provider: "USB", device: "C:\\cam" }).device).toBeTruthy();
    expect(validateCameraForm({ ...emptyForm(), name: "x", test_source: "media", test_media: "../etc" }).test_media).toBeTruthy();
  });
});

describe("camera request bodies", () => {
  it("sends credentials only when typed in", () => {
    expect(cameraBody(rtspForm())).not.toHaveProperty("password");
    const b = cameraBody(rtspForm({ username: "u", password: "p" }));
    expect(b).toMatchObject({ username: "u", password: "p", host: "192.168.1.64", rtsp_port: 554 });
  });

  it("omits RTSP fields for other providers and adds test settings", () => {
    const b = cameraBody({ ...emptyForm(), name: "Sim", test_simulate: "flaky" });
    expect(b).not.toHaveProperty("host");
    expect((b.settings as { test: { simulate: string } }).test.simulate).toBe("flaky");
  });

  it("PATCH contains only changes; empty password keeps stored credentials", () => {
    const c = camera();
    expect(cameraPatch(formFromCamera(c), c)).toEqual({});
    const p = cameraPatch({ ...formFromCamera(c), location: "Lobby" }, c);
    expect(p).toEqual({ location: "Lobby" });
    expect(cameraPatch({ ...formFromCamera(c), clear_credentials: true }, c)).toEqual({ clear_credentials: true });
    expect(cameraPatch({ ...formFromCamera(c), username: "a", password: "b" }, c)).toEqual({ username: "a", password: "b" });
  });
});

describe("camera status view", () => {
  it("does not show a stale or disabled camera as live", () => {
    expect(statusView(camera()).label).toBe("Ishlayapti");
    expect(statusView(camera({ monitoring: { reported_at: null, stale: true, runtime: null } })).label).toBe("Holat nomaʼlum");
    expect(statusView(camera({ is_enabled: false, status: "DISABLED", monitoring: null })).label).toBe("Oʻchirilgan");
    expect(statusView(camera({ is_enabled: false, status: "DRAFT", monitoring: null })).label).toBe("Qoralama");
  });

  it("allows recognition only after a passed quality test", () => {
    expect(canEnableRecognition(camera())).toBe(false);
    expect(canEnableRecognition(camera({ quality_status: "INCONCLUSIVE" }))).toBe(false);
    expect(canEnableRecognition(camera({ quality_status: "PASSED" }))).toBe(true);
  });
});

describe("camera departments", () => {
  it("sends departments on create and only changed ones on edit", () => {
    expect(cameraBody(rtspForm({ department_ids: ["b", "a"] })).department_ids).toEqual(["a", "b"]);
    const c = camera({ department_ids: ["a"] });
    expect(cameraPatch(formFromCamera(c), c)).toEqual({});
    expect(cameraPatch({ ...formFromCamera(c), department_ids: [] }, c)).toEqual({ department_ids: [] });
  });
});
