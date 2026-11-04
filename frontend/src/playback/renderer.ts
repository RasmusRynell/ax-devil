/**
 * Draws decoded frames on a canvas with WebGL2. Planar and semi-planar YUV frames are converted to RGB in a shader
 * from the matrix and range in the frame header; RGBA frames are drawn as they are. The canvas holds one cell per
 * lane: the frame is uploaded once and drawn into every cell, fitted keeping its aspect on a near-black field and
 * zoomed and panned by the viewer's viewport, and each lane's marks are laid out for its picture and painted over it
 * on the same context. What was drawn also answers where things are: the picture of each cell, the object under a
 * point, the outlines of selected and hovered objects. The export page draws with a fixed device pixel ratio and has
 * the renderer write each lane's name on its cell; the viewer follows the window's ratio and shows names in the DOM.
 */

import { TextAtlas } from "../overlay/atlas";
import { AtlasText } from "../overlay/atlasText";
import { canvasGlyphs } from "../overlay/canvasGlyphs";
import { OverlayPainter, type CellRect } from "../overlay/painter";
import type { Region } from "../present";
import type { LaneFrame, Picked, Selection, Viewport } from "./contract";
import { describeRow, LaneLayouts, type FoundRow } from "./laneLayouts";
import { NameTags } from "./nameTags";
import type { Matrix, VideoFrame } from "./protocol";
import { FRAGMENT, VERTEX } from "./shaders";
import { FIT, intersection, panBy, pictureRect, toImage, zoomAt } from "./viewport";

export type { CellRect } from "../overlay/painter";

/** Where a lane is drawn on the canvas, in device pixels. */
export type LaneCell = { lane: number; rect: CellRect };

/** A lane's cell and the zoomed picture in it, both in device pixels; the picture may reach past the cell. */
type Placed = { lane: number; cell: CellRect; picture: CellRect };

/** A point of the canvas in CSS pixels. */
export type Point = { x: number; y: number };

/**
 * An outline to draw over the picture of a lane, in CSS pixels of the canvas and clipped to the lane's cell.
 * `scale` is the shorter side of the lane's zoomed picture, which the stroke width is a fraction of.
 */
export type Outline = { lane: number; rect: CellRect; kind: "hover" | "selected"; scale: number };

/** What the renderer knows of where things are on the canvas; valid until the next draw. */
export type Geometry = Pick<VideoRenderer, "pick" | "pictures" | "boxes" | "zoomAt" | "panBy">;

/** WebGL2 cannot be had: the browser has no support, or has disabled it. */
export class RendererUnavailable extends Error {
  constructor(reason: string) {
    super(reason);
    this.name = "RendererUnavailable";
  }
}

/** Luma weights of red and blue by matrix; green is the rest. */
const LUMA_WEIGHTS: Record<Matrix, { kr: number; kb: number }> = {
  bt601: { kr: 0.299, kb: 0.114 },
  bt709: { kr: 0.2126, kb: 0.0722 },
};

/** The canvas token's near-black, `--color-canvas` (#0e0f10), as GL color components. */
const FIELD = { red: 0x0e / 255, green: 0x0f / 255, blue: 0x10 / 255 };

type Planes = { key: string; textures: WebGLTexture[] };

/** The part of `picture` inside `cell`, in the picture's CSS pixels; null when all of it is, so nothing is culled. */
function onScreen(picture: CellRect, cell: CellRect, dpr: number): Region | null {
  const shown = intersection(picture, cell);
  if (shown?.width === picture.width && shown.height === picture.height) return null;
  if (!shown) return { left: 0, top: 0, right: 0, bottom: 0 };
  return {
    left: (shown.x - picture.x) / dpr,
    top: (shown.y - picture.y) / dpr,
    right: (shown.x + shown.width - picture.x) / dpr,
    bottom: (shown.y + shown.height - picture.y) / dpr,
  };
}

/** One viewer's canvas: the video in every lane cell and each lane's marks over it. */
export class VideoRenderer {
  readonly #canvas: HTMLCanvasElement;
  readonly #gl: WebGL2RenderingContext;
  readonly #program: WebGLProgram;
  readonly #uniforms: Record<string, WebGLUniformLocation | null> = {};
  readonly #quad: WebGLVertexArrayObject;
  readonly #painter: OverlayPainter;
  #planes: Planes | undefined;
  #picture: { width: number; height: number } | undefined;
  #frame: VideoFrame | undefined;
  /** `null` is one cell for lane 0 covering the canvas. */
  #cells: readonly LaneCell[] | null = null;
  /** What each lane draws over the frame, by lane. */
  #lanes: readonly (LaneFrame | undefined)[] = [];
  #viewport: Viewport = FIT;
  /** Where the last draw put each lane. */
  #placed: readonly Placed[] = [];
  /** Called at the end of every draw, once the geometry is current. */
  onDrawn: (() => void) | null = null;
  /** The glyph atlas is the renderer's, so a restored context starts on a fresh page. */
  readonly #atlas = new TextAtlas(canvasGlyphs());
  readonly #text = new AtlasText(this.#atlas);
  readonly #layouts = new LaneLayouts(this.#text);
  readonly #tags = new NameTags(this.#text);
  /** The device pixel ratio to draw at; `undefined` follows the window's. */
  readonly #dpr: number | undefined;
  /** The name to write on each lane's cell, by lane; `null` writes none. */
  #names: readonly string[] | null = null;

  constructor(canvas: HTMLCanvasElement, dpr?: number) {
    this.#dpr = dpr;
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      stencil: true,
      powerPreference: "high-performance",
    });
    if (!gl) throw new RendererUnavailable("WebGL2 is not available");
    this.#canvas = canvas;
    this.#gl = gl;
    const program = gl.createProgram();
    for (const [type, source] of [
      [gl.VERTEX_SHADER, VERTEX],
      [gl.FRAGMENT_SHADER, FRAGMENT],
    ] as const) {
      const shader = gl.createShader(type) as WebGLShader;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
        throw new RendererUnavailable(gl.getShaderInfoLog(shader) ?? "a shader does not compile");
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    gl.useProgram(program);
    this.#program = program;
    this.#quad = gl.createVertexArray();
    gl.bindVertexArray(this.#quad);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, "corner");
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    for (const name of ["plane0", "plane1", "plane2", "mode", "kr", "kb", "limited", "crop"])
      this.#uniforms[name] = gl.getUniformLocation(program, name);
    gl.uniform1i(this.#uniforms.plane0, 0);
    gl.uniform1i(this.#uniforms.plane1, 1);
    gl.uniform1i(this.#uniforms.plane2, 2);
    gl.bindVertexArray(null);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    try {
      this.#painter = new OverlayPainter(gl);
    } catch (error) {
      throw new RendererUnavailable(
        error instanceof Error ? error.message : "overlays cannot be drawn",
      );
    }
  }

  /** Sets the lane cells, `null` for one cell covering the canvas, and draws again. */
  setCells(cells: readonly LaneCell[] | null): void {
    this.#cells = cells;
    this.redraw();
  }

  /** Writes `names[lane]` in the corner of each lane's cell, `null` for no names, and draws again. */
  setNames(names: readonly string[] | null): void {
    this.#names = names;
    this.redraw();
  }

  /** Zooms and pans every lane's picture and draws again. */
  setViewport(viewport: Viewport): void {
    if (viewport === this.#viewport) return;
    this.#viewport = viewport;
    this.redraw();
  }

  /** Sets the canvas to this many device pixels and draws the picture again. */
  resize(width: number, height: number): void {
    if (this.#canvas.width !== width) this.#canvas.width = width;
    if (this.#canvas.height !== height) this.#canvas.height = height;
    this.redraw();
  }

  /**
   * Draws the frame in every cell with each cell's lane marks from `lanes` (by lane) over it. The frame is uploaded
   * only when it is not the one drawn last.
   */
  present(frame: VideoFrame, lanes: readonly (LaneFrame | undefined)[] = []): void {
    this.#lanes = lanes;
    this.#layouts.keep(lanes.length);
    this.#painter.keepLanes(lanes.length);
    if (frame !== this.#frame) this.#upload(frame);
    this.redraw();
  }

  #upload(frame: VideoFrame): void {
    const gl = this.#gl;
    gl.useProgram(this.#program);
    const { format, width, height } = frame;
    const chromaWidth = Math.ceil(width / 2);
    const chromaHeight = Math.ceil(height / 2);
    const key = `${format}:${width}x${height}`;
    if (this.#planes?.key !== key) {
      this.#dropPlanes();
      // Full-resolution planes are sampled nearest so a pixel stays a pixel when zoomed. The half-resolution chroma
      // planes stay linear: 4:2:0 chroma is interpolated, as in the reference's swscale conversion.
      const texture = (internal: number, w: number, h: number, filter: number) => {
        const created = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, created);
        gl.texStorage2D(gl.TEXTURE_2D, 1, internal, w, h);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return created;
      };
      const textures =
        format === "rgba"
          ? [texture(gl.RGBA8, width, height, gl.NEAREST)]
          : format === "nv12"
            ? [
                texture(gl.R8, width, height, gl.NEAREST),
                texture(gl.RG8, chromaWidth, chromaHeight, gl.LINEAR),
              ]
            : [
                texture(gl.R8, width, height, gl.NEAREST),
                texture(gl.R8, chromaWidth, chromaHeight, gl.LINEAR),
                texture(gl.R8, chromaWidth, chromaHeight, gl.LINEAR),
              ];
      this.#planes = { key, textures };
    }
    const upload = (
      unit: number,
      w: number,
      h: number,
      bytesPerPixel: number,
      pixelFormat: number,
      plane: number,
    ) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, (this.#planes as Planes).textures[unit]);
      gl.pixelStorei(gl.UNPACK_ROW_LENGTH, frame.strides[plane] / bytesPerPixel);
      gl.texSubImage2D(
        gl.TEXTURE_2D,
        0,
        0,
        0,
        w,
        h,
        pixelFormat,
        gl.UNSIGNED_BYTE,
        frame.planes[plane],
      );
    };
    if (format === "rgba") upload(0, width, height, 4, gl.RGBA, 0);
    else {
      upload(0, width, height, 1, gl.RED, 0);
      if (format === "nv12") upload(1, chromaWidth, chromaHeight, 2, gl.RG, 1);
      else {
        upload(1, chromaWidth, chromaHeight, 1, gl.RED, 1);
        upload(2, chromaWidth, chromaHeight, 1, gl.RED, 2);
      }
    }
    gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
    const { kr, kb } = LUMA_WEIGHTS[frame.matrix];
    gl.uniform1i(this.#uniforms.mode, format === "rgba" ? 2 : format === "nv12" ? 1 : 0);
    gl.uniform1f(this.#uniforms.kr, kr);
    gl.uniform1f(this.#uniforms.kb, kb);
    gl.uniform1f(this.#uniforms.limited, frame.fullRange ? 0 : 1);
    this.#picture = { width, height };
    this.#frame = frame;
  }

  /** Draws the last frame and marks again, fitted to the canvas and cells as they are now. */
  redraw(): void {
    const gl = this.#gl;
    const { width: canvasWidth, height: canvasHeight } = this.#canvas;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.clearColor(FIELD.red, FIELD.green, FIELD.blue, 1);
    gl.clearStencil(0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT);
    this.#placed = [];
    const image = this.#picture;
    if (!image || !this.#planes) return;
    const cells = this.#cells ?? [
      { lane: 0, rect: { x: 0, y: 0, width: canvasWidth, height: canvasHeight } },
    ];
    const placed = cells
      .filter(({ rect }) => rect.width > 0 && rect.height > 0)
      .map(({ lane, rect }) => ({
        lane,
        cell: rect,
        picture: pictureRect(rect, image, this.#viewport),
      }));
    this.#placed = placed;
    const dpr = this.#dpr ?? devicePixelRatio;
    const drawings = this.#layouts.layOut(
      placed.map(({ lane, cell, picture }) => ({
        lane,
        frame: this.#lanes[lane],
        // CSS pixels, so a lane keeps its look at any pixel density.
        view: { width: picture.width / dpr, height: picture.height / dpr, dpr },
        visible: onScreen(picture, cell, dpr),
      })),
    );
    // After the lanes, whose layout may start a new atlas page: tags are laid out on whichever page is current.
    const names = this.#names;
    const tags = placed.map(({ lane, cell }) =>
      names?.[lane] === undefined
        ? undefined
        : this.#tags.drawing(lane, names[lane], dpr, cell.width / dpr, cell.height / dpr),
    );
    this.#painter.sync(this.#atlas);
    placed.forEach(({ cell, picture }, at) => {
      // Only the part of the picture inside the cell is drawn, so a high zoom needs no viewport past the canvas.
      const shown = intersection(picture, cell);
      if (shown) {
        gl.useProgram(this.#program);
        gl.bindVertexArray(this.#quad);
        gl.viewport(shown.x, canvasHeight - shown.y - shown.height, shown.width, shown.height);
        gl.uniform4f(
          this.#uniforms.crop,
          (shown.x - picture.x) / picture.width,
          (shown.y - picture.y) / picture.height,
          shown.width / picture.width,
          shown.height / picture.height,
        );
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        gl.bindVertexArray(null);
      }
      const drawing = drawings[at];
      if (drawing) this.#painter.draw(drawing, picture, cell);
      const tag = tags[at];
      if (tag) this.#painter.draw(tag, cell, cell);
    });
    this.onDrawn?.();
  }

  /** The zoomed picture in each lane's cell, in CSS pixels of the canvas, as drawn last. */
  pictures(): { lane: number; rect: CellRect }[] {
    return this.#placed.map(({ lane, picture }) => ({ lane, rect: this.#css(picture) }));
  }

  /** Whether the point (CSS pixels of the canvas) is on a lane's picture, not on the bars around it or between cells. */
  overPicture(x: number, y: number): boolean {
    const at = this.#device({ x, y });
    const hit = this.#cellAt(at);
    return hit !== undefined && toImage(hit.picture, at.x, at.y) !== null;
  }

  /** The object under the point (CSS pixels of the canvas) in the lane cell there, or null. */
  pick(x: number, y: number): Picked | null {
    const at = this.#device({ x, y });
    const hit = this.#cellAt(at);
    const inImage = hit && toImage(hit.picture, at.x, at.y);
    const frame = hit && this.#lanes[hit.lane];
    const found = hit && frame && inImage && this.#layouts.pick(hit.lane, inImage.x, inImage.y);
    if (!(hit && frame && found) || found.columns !== frame.columns) return null;
    return this.#picked(hit, frame, found);
  }

  /**
   * The outlines to draw: the selected object in every lane showing its source, and the hovered object in its lane.
   * An object a lane's sample does not have or its filter dropped gets none, and an outline is clipped to its cell.
   */
  boxes(selection: Selection, hover: Picked | null): Outline[] {
    const outlines: Outline[] = [];
    for (const placed of this.#placed) {
      const frame = this.#lanes[placed.lane];
      if (!frame) continue;
      const picture = this.#css(placed.picture);
      const scale = Math.min(picture.width, picture.height);
      const wanted: [string, "hover" | "selected"][] = [];
      if (selection && selection.source === frame.source)
        wanted.push([selection.entity, "selected"]);
      if (hover?.lane === placed.lane) wanted.push([hover.entity, "hover"]);
      for (const [entity, kind] of wanted) {
        const found = this.#layouts.find(placed.lane, entity);
        const rect = found && frame.columns === found.columns && this.#rowRect(placed, found);
        const clipped = rect && intersection(rect, placed.cell);
        if (clipped) outlines.push({ lane: placed.lane, rect: this.#css(clipped), kind, scale });
      }
    }
    return outlines;
  }

  /** `viewport` zoomed by `factor` toward `at` (CSS pixels of the canvas), by default the middle of the first lane cell. */
  zoomAt(viewport: Viewport, factor: number, at?: Point): Viewport {
    const image = this.#picture;
    const placed = (at && this.#cellAt(this.#device(at))) ?? this.#placed[0];
    if (!image || !placed) return viewport;
    const { cell } = placed;
    const target = at
      ? this.#device(at)
      : { x: cell.x + cell.width / 2, y: cell.y + cell.height / 2 };
    return zoomAt(viewport, cell, image, target.x, target.y, factor);
  }

  /** `viewport` moved by (`dx`, `dy`) CSS pixels of the lane cell at `at`. */
  panBy(viewport: Viewport, at: Point, dx: number, dy: number): Viewport {
    const image = this.#picture;
    const placed = this.#cellAt(this.#device(at)) ?? this.#placed[0];
    if (!image || !placed) return viewport;
    const scale = this.#scale();
    return panBy(viewport, placed.cell, image, dx * scale.x, dy * scale.y);
  }

  #picked(placed: Placed, frame: LaneFrame, { columns, row }: FoundRow): Picked {
    return {
      lane: placed.lane,
      source: frame.source,
      sample: frame.sample,
      row,
      ...describeRow(columns, row),
      box: this.#css(this.#rowRect(placed, { columns, row })),
    };
  }

  /** The row's box on the canvas in device pixels. */
  #rowRect({ picture }: Placed, { columns, row }: FoundRow): CellRect {
    const [left, top, right, bottom] = [0, 1, 2, 3].map((i) => columns.box[row * 4 + i]);
    return {
      x: picture.x + left * picture.width,
      y: picture.y + top * picture.height,
      width: (right - left) * picture.width,
      height: (bottom - top) * picture.height,
    };
  }

  #cellAt({ x, y }: Point): Placed | undefined {
    return this.#placed.find(
      ({ cell }) =>
        x >= cell.x && x < cell.x + cell.width && y >= cell.y && y < cell.y + cell.height,
    );
  }

  /** Device pixels per CSS pixel of the canvas, across and down. */
  #scale(): Point {
    return {
      x: this.#canvas.width / Math.max(1, this.#canvas.clientWidth),
      y: this.#canvas.height / Math.max(1, this.#canvas.clientHeight),
    };
  }

  #device({ x, y }: Point): Point {
    const scale = this.#scale();
    return { x: x * scale.x, y: y * scale.y };
  }

  #css(rect: CellRect): CellRect {
    const scale = this.#scale();
    return {
      x: rect.x / scale.x,
      y: rect.y / scale.y,
      width: rect.width / scale.x,
      height: rect.height / scale.y,
    };
  }

  /** Frees the GPU's copy of the picture and the overlay painter. */
  dispose(): void {
    this.#dropPlanes();
    this.#painter.dispose();
    this.#gl.deleteVertexArray(this.#quad);
    this.#gl.deleteProgram(this.#program);
  }

  #dropPlanes(): void {
    this.#planes?.textures.forEach((texture) => this.#gl.deleteTexture(texture));
    this.#planes = undefined;
    this.#frame = undefined;
  }
}
