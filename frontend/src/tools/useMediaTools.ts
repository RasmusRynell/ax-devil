import { useCallback, useEffect, useState } from "react";
import type { Selection } from "../playback/contract";
import { freshEvents, FRESH_LIST, type EventsState, type ListState } from "./listState";
import type { Place } from "./MediaToolsPanel";
import { EMPTY, topObject, type PageStack } from "./pageStack";

/**
 * What a viewer keeps of its media tools while the panel is closed: the width the user gave it, the pages open in it,
 * the state of its two lists and the lane they are about, and whether the selected object is outlined on the pictures (clearing the selection there
 * does not close its page). The selection and the pages move together through `navigate`, and the selection is the
 * object of the page on top. Another session, or pointing at objects being switched off, leaves no page open and
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
  const navigate = useCallback(
    (next: PageStack, at: Place) => {
      const object = topObject(next);
      setPages(next);
      setOutlined(true);
      session.select(object && { ...at, entity: object.entity });
    },
    [session],
  );
  useEffect(() => {
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
