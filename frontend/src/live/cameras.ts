/** The workspace's live streams as the Live view lists them: grouped by camera. */

import { useMemo } from "react";
import { reasonOf } from "../errors";
import type { ItemState, LiveOverlayKind, LiveShown, NewLiveWire } from "../generated/api";
import { useDoc } from "../session/session";
import { useShownItems } from "../viewer/useViewers";

/** What the Add Live Stream dialog starts from: the stream whose connection a new one copies. */
export type AddSeed = { like?: string };

/** Where a stream stands before its viewer can open: it is resolving or failed to. */
export type StreamState = "ready" | "resolving" | "failed";

/** One live stream of a camera. */
export type Stream = {
  /** The workspace item. */
  id: string;
  /** What the user called it, else its host as entered. */
  label: string;
  /** Whether its viewer is open, which is what streaming is. */
  open: boolean;
  state: StreamState;
  /** `null` until the item has resolved, which is when it can start. */
  overlay: LiveOverlayKind | null;
  /** The overlay's lane name, which names the stream where its recipe is not known. */
  lane: string | null;
  /** How it was added, for the owner; `null` for a watcher and for a stream not resolved yet. */
  recipe: NewLiveWire | null;
  /** Why it did not resolve, when it failed. */
  reason: string;
  /** The lines its row opens: the MQTT broker and port, else where the stream is (its host, and its head when not 1),
   * with `$NAME` references as they expand. */
  info: string[];
  /** Whether the window may edit it: it is told the stream's recipe. */
  editable: boolean;
  /** The generation of its Content, which a new login check is made for; 0 until it has resolved. */
  generation: number;
};

/** A camera and its streams, in workspace order. */
export type Camera = {
  key: string;
  /** What the camera's row is called: the first label a stream was given, else its address. */
  name: string;
  /** Where it is: the host as entered, or the label of a stream whose recipe is not known. */
  address: string;
  /** The names of its host, lower-cased: as entered and as a `$NAME` reference expands; none where the camera is known
   * by its label only. A scan's finds are matched to the camera by any of them. */
  hosts: readonly string[];
  streams: Stream[];
};

/** The text of a stream's row: the MQTT broker and port, else the host and, when not 1, the camera head. */
function infoOf(recipe: NewLiveWire | null, shown: LiveShown | undefined): string[] {
  const overlay = recipe?.overlay;
  const entered = overlay?.mode === "mqtt" ? overlay.broker_host : undefined;
  const broker = (shown ? shown.broker_host : entered)?.trim();
  if (overlay?.mode === "mqtt" && broker)
    return [overlay.broker_port ? `${broker}:${overlay.broker_port}` : broker];
  const host = (shown ? shown.host : recipe?.host)?.trim();
  if (!host) return [];
  return [recipe?.head && recipe.head !== 1 ? `${host} · head ${recipe.head}` : host];
}

/**
 * The live stream an item is, if it is one, whatever it has resolved to so far. `recipe` is how the stream resolved
 * before, which an item resolving again or failing keeps its camera by.
 */
function streamOf(
  { id, label, kind, open, resolution, recipe: stored, shown }: ItemState,
  recipe: NewLiveWire | null,
): Stream | null {
  const base = {
    id,
    label,
    open,
    overlay: null,
    lane: null,
    recipe,
    info: infoOf(recipe, shown),
    reason: "",
    editable: stored?.type === "Live",
    generation: 0,
  };
  if (resolution.status === "Resolved") {
    const { content } = resolution;
    return content.kind === "LiveVideo"
      ? {
          ...base,
          state: "ready",
          overlay: content.overlay,
          lane: content.lanes[0] ?? null,
          recipe: content.recipe,
          info: infoOf(content.recipe, shown),
          generation: content.generation,
        }
      : null;
  }
  if (kind !== "live") return null;
  if (resolution.status === "Pending") return { ...base, state: "resolving" };
  return { ...base, state: "failed", reason: reasonOf(resolution) };
}

/** The recipe a live item is grouped by: how it resolved, else, for a seat that may see it, as it is stored. */
const groupedBy = (item: ItemState, recipe: NewLiveWire | null): NewLiveWire | null =>
  recipe ?? (item.recipe?.type === "Live" ? item.recipe : null);

/**
 * The cameras of `items` in the order their first stream appears. A camera is the streams whose recipes name the same
 * host (whatever the case, and as the Session reads a `$NAME` reference: `shown`) and device port, named
 * by the first label one of them was given, else by the host. Streams without a recipe (a watcher's) are grouped by
 * their label. `known` holds the recipes items resolved with before, by item.
 */
export function camerasOf(
  items: readonly ItemState[],
  known: ReadonlyMap<string, NewLiveWire> = new Map(),
): Camera[] {
  const cameras = new Map<string, Camera>();
  const labelled = new Set<string>();
  for (const item of items) {
    const stream = streamOf(item, known.get(item.id) ?? null);
    if (!stream) continue;
    const recipe = groupedBy(item, stream.recipe);
    const host = recipe?.host?.trim() ?? "";
    const address = host || item.label.trim();
    const value = (item.shown?.host || host).toLowerCase();
    const key = host ? `host:${value}:${recipe?.port ?? 0}` : `label:${address.toLowerCase()}`;
    const camera = cameras.get(key) ?? {
      key,
      name: address,
      address,
      hosts: host ? [...new Set([host.toLowerCase(), value])] : [],
      streams: [],
    };
    const label = recipe?.label?.trim();
    if (label && !labelled.has(key)) {
      labelled.add(key);
      camera.name = label;
    }
    camera.streams.push(stream);
    cameras.set(key, camera);
  }
  return [...cameras.values()];
}

/**
 * The fields the Add Live Stream dialog starts from. `like` copies the connection of that stream's recipe (host,
 * user, protocol, ports and RTSP route) and nothing else, so the copy gets its own name, sizes and overlay. The password is not
 * copied: a recipe never holds a literal one, so the dialog sends `like` and the backend fills the blank password in.
 */
export function seedOf(seed: AddSeed, items: readonly ItemState[]): Partial<NewLiveWire> {
  const recipe = camerasOf(items)
    .flatMap((camera) => camera.streams)
    .find((stream) => stream.id === seed.like)?.recipe;
  if (!recipe) return {};
  const { host, username, protocol, port, rtsp_port, rtsp_route } = recipe;
  return { host, username, protocol, port, rtsp_port, rtsp_route };
}

/**
 * Whether the stream is connected: its viewer is open and some pane shows it (`viewed` is the items with a pane).
 * An open stream with no pane has no connection, and Start opens one.
 */
export const running = (stream: Stream, viewed: ReadonlySet<string>): boolean =>
  stream.open && viewed.has(stream.id);

/** How many of `cameras`' streams are connected. */
export const streamingCount = (cameras: readonly Camera[], viewed: ReadonlySet<string>): number =>
  cameras.reduce((sum, { streams }) => sum + streams.filter((s) => running(s, viewed)).length, 0);

/** How many live streams are connected: their viewer is open and in a pane. */
export function useStreaming(): number {
  const items = useDoc()?.workspace.items;
  const viewed = useShownItems();
  return useMemo(() => streamingCount(camerasOf(items ?? []), viewed), [items, viewed]);
}
