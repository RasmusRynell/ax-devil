import { useCallback, useEffect, useRef, useState } from "react";
import type { Selection } from "../lanes/contract";
import { freshEvents, freshList, type EventsState, type ListState } from "./listState";
import type { Place } from "./MediaToolsPanel";
import type { ObjectSource } from "./ObjectsList";
import { useEventNavigation } from "./useEventNavigation";
import { EMPTY, jump, topObject, type PageStack } from "./pageStack";

/**
 * What a viewer keeps of its media tools while the panel is closed: the width the user gave it, the pages open in it,
 * the state of its two lists and the lane they are about, and whether the selected object is outlined on the pictures (clearing the selection there
 * does not close its page). The selection and the pages move together through `navigate`, and the selection is the
 * object of the page on top. The pages are about one lane's file: a page that opens on another lane (an object picked on
 * that lane's picture) leaves the pages of the lane before it behind. Another session, or pointing at objects being switched off, leaves no page open and
 * nothing selected. `panel` is what the panel is given of all that.
 */
export function useMediaTools(
  session: { select(selection: Selection): void },
  objectDetails: boolean,
  visible = false,
  source?: Pick<ObjectSource, "files" | "frameTime">,
) {
  const [width, onWidth] = useState<number | null>(null);
  const [pages, setPages] = useState<PageStack>(EMPTY);
  const [list, onList] = useState<ListState>(freshList);
  const [events, onEvents] = useState<EventsState>(freshEvents);
  const [chosen, onChosen] = useState<number | null>(null);
  const [outlined, setOutlined] = useState(true);
  // A hidden pick is provisional until the panel opens.
  const pending = useRef<Selection>(null);
  const pagePlace = useRef<Place | null>(null);
  const navigate = useCallback(
    (next: PageStack, at: Place) => {
      const top = next.entries.at(-1);
      // An index into one file names nothing in another, so what lay under a page of a new lane is not kept.
      const moved = pagePlace.current !== null && pagePlace.current.lane !== at.lane;
      const shown: PageStack =
        moved && top && next.entries.length > 1
          ? { ...next, entries: [{ key: top.key, origin: null }], mark: null }
          : next;
      pagePlace.current = shown.entries.length > 0 ? at : null;
      const object = topObject(shown);
      onChosen(at.lane);
      setPages(shown);
      setOutlined(true);
      session.select(object && { ...at, entity: object.entity });
    },
    [session],
  );
  const remap = useCallback(
    (next: PageStack) => {
      const before = topObject(pages);
      const after = topObject(next);
      const at = pagePlace.current;
      setPages(next);
      if (before?.entity !== after?.entity)
        session.select(after && at ? { ...at, entity: after.entity } : null);
      if (next.entries.length === 0) pagePlace.current = null;
    },
    [pages, session],
  );
  useEventNavigation(source, chosen, pages, remap, visible);
  const pick = (selection: Selection) => {
    const cancelled = pending.current;
    pending.current = null;
    if (!selection) {
      if (cancelled) {
        const object = topObject(pages);
        session.select(
          object && pagePlace.current ? { ...pagePlace.current, entity: object.entity } : null,
        );
      }
      setOutlined(false);
      return;
    }
    if (visible) navigate(jump(pages, { kind: "object", entity: selection.entity }), selection);
    else {
      pending.current = selection;
      session.select(selection);
      setOutlined(true);
    }
  };
  const chooseLane = useCallback(
    (lane: number) => {
      if (pending.current?.lane === lane) {
        onChosen(lane);
        return;
      }
      const cancelled = pending.current;
      pending.current = null;
      onChosen(lane);
      if (pagePlace.current?.lane !== lane) {
        pagePlace.current = null;
        setPages((current) => ({ ...current, entries: [], mark: null }));
        session.select(null);
      } else if (cancelled) {
        navigate(pages, pagePlace.current);
      }
    },
    [session, navigate, pages],
  );
  useEffect(() => {
    const selection = pending.current;
    if (!visible || !selection) return;
    pending.current = null;
    navigate(jump(pages, { kind: "object", entity: selection.entity }), selection);
  }, [visible, pages, navigate]);
  useEffect(() => {
    pending.current = null;
    pagePlace.current = null;
    setPages(EMPTY);
    onList(freshList());
    onEvents(freshEvents());
    onChosen(null);
    setOutlined(true);
    if (!objectDetails) session.select(null);
  }, [session, objectDetails]);
  return {
    pages,
    pick,
    chooseLane,
    outlined,
    navigate,
    panel: {
      pages,
      onNavigate: navigate,
      list,
      onList,
      events,
      onEvents,
      chosen,
      onChosen: chooseLane,
      width,
      onWidth,
    },
  };
}
