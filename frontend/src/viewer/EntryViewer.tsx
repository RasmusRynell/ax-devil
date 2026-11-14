import { useCallback } from "react";
import type { ContentInfo, EntryInfo, LanePolicyWire } from "../generated/api";
import { useCan, useCommand } from "../session/session";
import type { PreparedEntry } from "../playback/nextEntry";
import { VideoViewer } from "../playback/VideoViewer";
import type { ViewerProps } from "../viewers";
import { useHidden } from "./useViewers";

/**
 * Plays one entry of an item's Content with what the workspace says about it: the lanes the user hid are left out,
 * and a seat that may change the workspace can hide a lane from the lane's own menu and its matching is kept in the
 * item's recipe.
 */
export function EntryViewer({
  item,
  viewer,
  content,
  entry,
  next,
}: {
  item: string;
  viewer: number;
  content: Pick<ContentInfo, "id" | "generation" | "display_name">;
  entry: EntryInfo;
  /** The entry a playlist steps to from this one, for the backend to prepare. */
  next?: PreparedEntry | null;
}) {
  const hidden = useHidden();
  const command = useCommand();
  const changes = useCan("content");
  const hideLane = useCallback(
    (lane: string) =>
      // A refused hide leaves the lane on screen, which says so.
      void command({
        type: "content",
        command: { type: "setHidden", target: { entry: entry.id, lane }, hidden: true },
      }).catch(() => undefined),
    [command, entry.id],
  );
  const keepMatching = useCallback(
    (slot: string, policy: LanePolicyWire) =>
      void command({
        type: "content",
        command: { type: "setLaneMatching", item, slot, policy },
      }).catch(() => undefined),
    [command, item],
  );
  return (
    <VideoViewer
      item={item}
      viewer={viewer}
      content={content}
      entry={entry}
      next={next}
      hidden={hidden}
      onHideLane={changes ? hideLane : undefined}
      onLaneMatching={changes ? keepMatching : undefined}
    />
  );
}

/** The viewer of one video: its one entry. */
export function SeekableVideoViewer({ item, viewer, content }: ViewerProps<"SeekableVideo">) {
  return <EntryViewer item={item} viewer={viewer} content={content} entry={content.entry} />;
}
