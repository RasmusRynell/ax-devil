/** Decodes the JPEG frames a viewer on another machine is sent into the RGBA frames the renderer draws. */

import type { JpegFrame, VideoFrame } from "./protocol";

let context: OffscreenCanvasRenderingContext2D | null = null;

/** The RGBA frame `frame` decodes to. JPEG is full-range BT.601, which the browser's decoder already applied. */
export async function decodeJpeg(frame: JpegFrame): Promise<VideoFrame> {
  const { jpeg, width, height, ...identity } = frame;
  const bitmap = await createImageBitmap(new Blob([jpeg.slice()], { type: "image/jpeg" }), {
    colorSpaceConversion: "none",
    premultiplyAlpha: "none",
  });
  try {
    if (bitmap.width !== width || bitmap.height !== height)
      throw new Error(`a ${width}x${height} frame decoded to ${bitmap.width}x${bitmap.height}`);
    if (context?.canvas.width !== width || context.canvas.height !== height)
      context = new OffscreenCanvas(width, height).getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("no 2D canvas to decode frames on");
    context.drawImage(bitmap, 0, 0);
    const pixels = new Uint8Array(context.getImageData(0, 0, width, height).data.buffer);
    return {
      ...identity,
      width,
      height,
      format: "rgba",
      matrix: "bt601",
      fullRange: true,
      strides: [width * 4, 0, 0],
      planes: [pixels, new Uint8Array(), new Uint8Array()],
      byteLength: pixels.byteLength,
    };
  } finally {
    bitmap.close();
  }
}
