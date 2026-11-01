import { useEffect, useRef, useState } from "react";
import type { Scope } from "../commands";
import type { ViewState } from "../generated/api";
import { useSession, type SessionApi } from "./session";

/** What the window shows before anyone has chosen anything. */
export const INITIAL_VIEW: ViewState = { sidebarView: "workspace", sidebarOpen: true };

/** What this tab may change: its content (private and owner), its view (also an exploring watcher), the app (always). */
function allowed(session: SessionApi): Record<Scope, boolean> {
  const watching = session.state === "watcher";
  const view =
    !watching || session.snapshot?.mode === "exploring" || !!session.snapshot?.options.explore;
  return { content: !watching, view, app: true };
}

/** Which scopes of commands work for this tab; the others are shown disabled. */
export function useAllowed(): Record<Scope, boolean> {
  return allowed(useSession());
}

export interface ViewHandle {
  /** What the window shows. */
  state: ViewState;
  /** Changes it; for a following watcher that may explore, the first change starts exploring. */
  set: (next: ViewState) => void;
  /** False for a watcher that cannot change its view: the controls are disabled. */
  editable: boolean;
}

/**
 * The view state of the window, and the only place that branches on private, owner, following and exploring.
 *
 * Private: local state. Owner: local state, published on every change. Following: the presenter's, read only.
 * Exploring: a local copy that starts from the presenter's view and is sent as `SetView`. The server's answer
 * replaces the copy only once it has caught up with the last edit sent (`viewSeq >= seq`); edits are numbered above the server's own count, which outlives a reload, so an older snapshot
 * never undoes a newer edit that is still on its way. Popups, dialogs and settings are not part of it.
 */
export function useViewState(): ViewHandle {
  const session = useSession();
  const { state: role, snapshot, connection } = session;
  const { publish, setView, explore } = session;
  const [local, setLocal] = useState(INITIAL_VIEW);
  const [explored, setExplored] = useState({ state: INITIAL_VIEW, seq: 0 });
  const sent = useRef(0);
  const owner = role === "owner";
  const online = connection === "online";

  // The owner's window is the source: publish it when the session starts, after a reconnect and on every change.
  useEffect(() => {
    if (owner && online) publish(local);
  }, [owner, online, local, publish]);

  const watching = role === "watcher" && snapshot;
  const editable = allowed(session).view;
  if (!watching) return { state: local, set: setLocal, editable };
  const exploring = snapshot.mode === "exploring";
  const state = !exploring || snapshot.viewSeq >= explored.seq ? snapshot.view : explored.state;
  const set = (next: ViewState) => {
    if (!editable) return;
    if (!exploring) explore();
    // A resumed seat keeps the sequence the server last accepted, which may be ahead of this tab's own count.
    sent.current = Math.max(sent.current, snapshot.viewSeq) + 1;
    setExplored({ state: next, seq: sent.current });
    setView(next, sent.current);
  };
  return { state, set, editable };
}
