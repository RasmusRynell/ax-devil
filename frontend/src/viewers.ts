import type { ComponentType } from "react";
import type { ContentInfo } from "./generated/api";
import { VideoViewer } from "./playback/VideoViewer";
import type { Hidden } from "./viewer/hidden";
import { PlaylistViewer } from "./viewer/PlaylistViewer";

type Kind = ContentInfo["kind"];
/** Where a viewer keeps what it shows while it is not on screen: its position, say. Closing the viewer forgets it. */
export type ViewerMemory = { saved: unknown; save(state: unknown): void };
export type ViewerProps<K extends Kind = Kind> = {
  content: Extract<ContentInfo, { kind: K }>;
  memory: ViewerMemory;
  /** What the user hid in the workspace tree, which a viewer leaves out. */
  hidden: Hidden;
};

/**
 * The viewer for each kind of Content. Adding a kind to the backend's `ContentInfo` fails to compile here until it
 * has one.
 */
export const VIEWERS: { [K in Kind]: ComponentType<ViewerProps<K>> } = {
  Playlist: PlaylistViewer,
  SeekableVideo: VideoViewer,
};

/** The viewer component for this Content. */
export function viewerFor(content: ContentInfo): ComponentType<ViewerProps> {
  return VIEWERS[content.kind] as ComponentType<ViewerProps>;
}
