import { useCallback, useEffect, useRef, useState } from "react";
import type { Selection } from "../lanes/contract";
import { freshEvents, FRESH_LIST, type EventsState, type ListState } from "./listState";
import type { Place } from "./MediaToolsPanel";
import { EMPTY, topObject, type PageStack } from "./pageStack";

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
) {
  const [width, onWidth] = useState<number | null>(null);
  const [pages, setPages] = useState<PageStack>(EMPTY);
  const [list, onList] = useState<ListState>(FRESH_LIST);
  const [events, onEvents] = useState<EventsState>(freshEvents);
  const [chosen, onChosen] = useState<number | null>(null);
  const [outlined, setOutlined] = useState(true);
  // The lane the open pages are about.
  const pagesLane = useRef<number | null>(null);
  const navigate = useCallback(
    (next: PageStack, at: Place) => {
      const top = next.entries.at(-1);
      // An index into one file names nothing in another, so what lay under a page of a new lane is not kept.
      const moved = pagesLane.current !== null && pagesLane.current !== at.lane;
      const shown: PageStack =
        moved && top && next.entries.length > 1
          ? { ...next, entries: [{ key: top.key, origin: null }], mark: null }
          : next;
      pagesLane.current = shown.entries.length > 0 ? at.lane : null;
      const object = topObject(shown);
      setPages(shown);
      setOutlined(true);
      session.select(object && { ...at, entity: object.entity });
    },
    [session],
  );
  useEffect(() => {
    pagesLane.current = null;
    setPages(EMPTY);
    onList(FRESH_LIST);
    onEvents(freshEvents());
    onChosen(null);
    setOutlined(true);
    if (!objectDetails) session.select(null);
  }, [session, objectDetails]);
  return {
    pages,
    outlined,
    setOutlined,
    navigate,
    panel: {
      pages,
      onNavigate: navigate,
      list,
      onList,
      events,
      onEvents,
      chosen,
      onChosen,
      width,
      onWidth,
    },
  };
}
