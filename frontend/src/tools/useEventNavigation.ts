import { answersEvent, sameEventIdentity } from "../inspect/fileEvents";
import { useEffect, useSyncExternalStore } from "react";
import type { ObjectSource } from "./ObjectsList";
import { itemOf, type EventItem } from "./eventItem";
import type { PageKey, PageStack } from "./pageStack";

const noSubscribe = () => () => {};

/** Replaces every occurrence of one event, including the list mark; absent events leave the stack. */
function mapped(pages: PageStack, previous: EventItem, next: EventItem | null): PageStack {
  const map = (key: PageKey): PageKey | null =>
    key.kind === "event" && sameEventIdentity(key.event, previous)
      ? next && { kind: "event", event: next }
      : key;
  return {
    ...pages,
    entries: pages.entries.flatMap((entry) => {
      const key = map(entry.key);
      return key ? [{ ...entry, key }] : [];
    }),
    mark: pages.mark && map(pages.mark),
  };
}

/** The navigation owner remaps all stacked events and marks, even while their components are unmounted. */
export function useEventNavigation(
  source: Pick<ObjectSource, "files" | "frameTime"> | undefined,
  lane: number | null,
  pages: PageStack,
  onPages: (pages: PageStack) => void,
  visible: boolean,
) {
  const files = source?.files;
  const held = useSyncExternalStore(files?.subscribe ?? noSubscribe, () =>
    lane === null ? null : (files?.events.event(lane) ?? null),
  );
  const revision = useSyncExternalStore(files?.subscribe ?? noSubscribe, () =>
    lane === null ? -1 : (files?.events.revision(lane) ?? -1),
  );
  useEffect(() => {
    if (!source || lane === null) return;
    const keys = [...pages.entries.map(({ key }) => key), ...(pages.mark ? [pages.mark] : [])];
    const events = keys.flatMap((key) =>
      key.kind === "event" && key.event.anchor ? [key.event] : [],
    );
    const unstamped = events.find((event) => event.revision === undefined);
    if (unstamped) {
      onPages(mapped(pages, unstamped, { ...unstamped, revision }));
      return;
    }
    const stale = events.find((event) => event.revision !== revision);
    const top = pages.entries.at(-1)?.key;
    const target =
      stale ?? (visible && top?.kind === "event" && top.event.anchor ? top.event : undefined);
    if (!target) {
      source.files.events.want(lane, null);
      return;
    }
    const found = answersEvent(held, target, revision) ? held : null;
    if (
      found &&
      (!found.detail ||
        stale ||
        (found.detail &&
          (found.detail.row.frame !== target.frame || found.detail.row.event !== target.id)))
    ) {
      onPages(
        mapped(
          pages,
          target,
          found.detail ? itemOf(found.detail.row, (at) => source.frameTime(at), revision) : null,
        ),
      );
      return;
    }
    source.files.events.want(lane, target.id, 64, {
      anchor: target.anchor!,
      frame: target.frame,
      revision: target.revision!,
    });
  }, [source, lane, pages, onPages, visible, held, revision]);
  useEffect(
    () => () => {
      if (lane !== null) files?.events.want(lane, null);
    },
    [files, lane],
  );
}
