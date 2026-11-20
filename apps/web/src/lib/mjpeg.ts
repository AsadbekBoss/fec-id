/**
 * Incremental parser for a multipart/x-mixed-replace (MJPEG) body read with fetch(). Each part
 * carries a Content-Length header, so frames are cut by length, not by scanning JPEG markers.
 */
const MAX_BUFFER = 8 * 1024 * 1024;

function indexOfHeaderEnd(buf: Uint8Array): number {
  for (let i = 0; i + 3 < buf.length; i++) {
    if (buf[i] === 13 && buf[i + 1] === 10 && buf[i + 2] === 13 && buf[i + 3] === 10) return i;
  }
  return -1;
}

export class MjpegParser {
  private buf: Uint8Array<ArrayBuffer> = new Uint8Array(0);

  /** Feed the next network chunk; returns every complete JPEG it finishes. */
  push(chunk: Uint8Array): Uint8Array<ArrayBuffer>[] {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf);
    merged.set(chunk, this.buf.length);
    this.buf = merged;

    const frames: Uint8Array<ArrayBuffer>[] = [];
    for (;;) {
      const end = indexOfHeaderEnd(this.buf);
      if (end < 0) break;
      const header = new TextDecoder("latin1").decode(this.buf.subarray(0, end));
      const m = /content-length:\s*(\d+)/i.exec(header);
      const start = end + 4;
      if (!m) {
        this.buf = this.buf.slice(start); // not a frame header: skip it and resync
        continue;
      }
      const n = Number(m[1]);
      if (this.buf.length < start + n) break; // wait for the rest of this frame
      frames.push(this.buf.slice(start, start + n));
      this.buf = this.buf.slice(start + n);
    }
    if (this.buf.length > MAX_BUFFER) this.buf = new Uint8Array(0); // garbage: drop it
    return frames;
  }
}
