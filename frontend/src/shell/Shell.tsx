import { useEffect, useState } from "react";
import { COMMANDS, scopeOf } from "../commands";
import type { SidebarView } from "../generated/api";
import type { MenuCommand } from "../kit/Menu";
import { matchesShortcut } from "../kit/shortcut";
import type { Scopes } from "../session/cursor";
import { Presence } from "../session/Presence";
import { useAllowed, useCommand, useView } from "../session/session";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import { useFrontViewer, useViewers } from "../viewer/useViewers";
import { ViewerHost } from "../viewer/ViewerHost";
import { ActivityBar } from "./ActivityBar";
import { Banner } from "./Banner";
import { TitleBar } from "./TitleBar";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import type { ActivityItem } from "./views";
import "./windowBridge";

const TEXT_INPUTS = new Set(["text", "search", "number", "email", "url", "tel", "password"]);

/** Whether keys without Ctrl or Alt belong to the viewer: focus is on the page or the central area, and not in a text field. */
function viewerOwnsPlainKeys(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return true;
  const typing =
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    (target instanceof HTMLInputElement && TEXT_INPUTS.has(target.type));
  return !typing && (target === document.body || target.closest("main") !== null);
}

/**
 * The application layout: menu bar over the activity bar, sidebar and central area. What the window shows comes from
 * the session's view, so a watcher that follows the presenter shows the presenter's.
 */
export function Shell() {
  const [sidebar, setSidebar] = useView("sidebar");
  const { active } = useViewers();
  useFrontViewer();
  const allowed = useAllowed();
  const command = useCommand();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const [viewerActions, setViewerActions] = useState<ViewerActions>(NO_ACTIONS);

  const addVideo = () => {
    const bridge = window.axDevilWindow;
    if (!bridge) return setNotice("Adding a video needs the ax-devil desktop app");
    bridge
      .pickVideos()
      .then((paths) =>
        paths.length
          ? command({
              type: "content",
              command: {
                type: "addItems",
                items: paths.map((path) => ({ type: "Video" as const, path })),
                open: true,
              },
            })
          : undefined,
      )
      .then(() => setNotice(null))
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : String(error)));
  };

  const startSharing = () => {
    setSidebar({ view: "sessions", open: true });
    command({ type: "control", control: { type: "share", access: "watch" } }).then(
      () => setNotice(null),
      (error: unknown) => setNotice(error instanceof Error ? error.message : String(error)),
    );
  };

  // Commands with behavior; every other command is a placeholder that does nothing yet. The open viewer adds its own.
  const actions: Record<string, () => void> = {
    sidebar: () => setSidebar({ ...sidebar, open: !sidebar.open }),
    settings: () => setSettingsOpen(true),
    "add-video": addVideo,
  };
  const find = (id: string) =>
    allowed[scopeOf(id)] ? (actions[id] ?? viewerActions[id]) : undefined;
  const run = (id: string) => find(id)?.();
  const select = (item: MenuCommand) => {
    if (item.kind === "check")
      setChecked((now) => {
        const next = new Set(now);
        if (!next.delete(item.id)) next.add(item.id);
        return next;
      });
    else run(item.id);
  };
  const activate = (item: ActivityItem) => {
    if (item.opens === "command") run(item.id);
    else if (sidebar.open && sidebar.view === item.id) setSidebar({ ...sidebar, open: false });
    else setSidebar({ view: item.id as SidebarView, open: true });
  };
  // What each region of this window shows, so a cursor lands by region only where the same thing is shown.
  const scopes: Scopes = { sidebar: sidebar.view, main: active?.id ?? "welcome" };

  useEffect(() => {
    /** The command this key press runs, if the shell handles it. */
    const handled = (event: KeyboardEvent) => {
      const match = [...COMMANDS.values()].find(
        (item) => item.shortcut && find(item.id) && matchesShortcut(event, item.shortcut),
      );
      const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
      return match && !(plain && !viewerOwnsPlainKeys(event.target)) ? match : undefined;
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const match = handled(event);
      if (!match) return;
      event.preventDefault();
      if (!event.repeat || match.repeats) run(match.id);
    };
    // Space on a focused button clicks it when released, which would undo the command it just ran.
    const onKeyUp = (event: KeyboardEvent) => {
      if (handled(event)) event.preventDefault();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
    };
  });

  return (
    <ViewerActionsContext.Provider value={setViewerActions}>
      <div className="flex h-screen flex-col">
        <h1 className="sr-only">ax-devil</h1>
        <TitleBar
          checked={checked}
          wired={new Set([...Object.keys(actions), ...Object.keys(viewerActions)])}
          onSelect={select}
          onStartSharing={startSharing}
        />
        {notice && (
          <Banner role="alert" onDismiss={() => setNotice(null)}>
            {notice}
          </Banner>
        )}
        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <ActivityBar active={sidebar.open ? sidebar.view : null} onSelect={activate} />
          {sidebar.open && <Sidebar view={sidebar.view} onRun={run} />}
          <main
            data-cursor-region="main"
            className={`flex min-h-0 min-w-0 flex-1 overflow-auto ${sidebar.open ? "max-sm:hidden" : ""}`}
          >
            {active ? <ViewerHost /> : <Welcome onRun={run} />}
          </main>
        </div>
        <Presence scopes={scopes} />
        {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
      </div>
    </ViewerActionsContext.Provider>
  );
}
