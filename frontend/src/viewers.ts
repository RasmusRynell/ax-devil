import type { ComponentType } from "react";
import type { ContentInfo } from "./generated/api";
import { VideoViewer } from "./playback/VideoViewer";

type Kind = ContentInfo["kind"];
/** What a viewer is given: its item, whose id keys what the window shows of it, and the item's Content. */
export type ViewerProps<K extends Kind = Kind> = {
  item: string;
  content: Extract<ContentInfo, { kind: K }>;
};

/**
 * The viewer for each kind of Content. Adding a kind to the backend's `ContentInfo` fails to compile here until it
 * has one.
 */
export const VIEWERS: { [K in Kind]: ComponentType<ViewerProps<K>> } = {
  SeekableVideo: VideoViewer,
};

/** The viewer component for this Content. */
export function viewerFor(content: ContentInfo): ComponentType<ViewerProps> {
  return VIEWERS[content.kind] as ComponentType<ViewerProps>;
}
