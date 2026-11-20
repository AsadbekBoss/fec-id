import { describe, expect, it } from "vitest";
import { MjpegParser } from "./mjpeg";

const enc = new TextEncoder();
const part = (payload: number[]) =>
  new Uint8Array([
    ...enc.encode(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${payload.length}\r\n\r\n`),
    ...payload,
    ...enc.encode("\r\n"),
  ]);

describe("MjpegParser", () => {
  it("returns whole frames, even when chunks split headers and payloads anywhere", () => {
    const a = [0xff, 0xd8, 13, 10, 13, 10, 0xff, 0xd9]; // payload containing CRLFCRLF
    const b = [0xff, 0xd8, 1, 2, 3, 0xff, 0xd9];
    const all = new Uint8Array([...part(a), ...part(b)]);
    for (let cut = 1; cut < all.length; cut += 3) {
      const p = new MjpegParser();
      const frames = [...p.push(all.subarray(0, cut)), ...p.push(all.subarray(cut))];
      expect(frames.map((f) => [...f])).toEqual([a, b]);
    }
  });

  it("skips a part without Content-Length", () => {
    const p = new MjpegParser();
    const junk = enc.encode("--frame\r\nContent-Type: text/plain\r\n\r\n");
    const frames = p.push(new Uint8Array([...junk, ...part([1, 2])]));
    expect(frames.map((f) => [...f])).toEqual([[1, 2]]);
  });
});
