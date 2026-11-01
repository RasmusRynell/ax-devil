import { useEffect, useRef, useState } from "react";
import type { MenuCommand } from "../kit/Menu";
import { shortcutOf } from "../kit/shortcut";
import { COMMANDS, SETTINGS } from "../generated/settings";
import { useSettings } from "../settings";
import { ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import { ViewerHost } from "../viewer/ViewerHost";
import { addVideos } from "../workspace/client";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import { ActivityBar } from "./ActivityBar";
import { AddPlaylistDialog } from "./AddPlaylistDialog";
import { Banner } from "./Banner";
import { TitleBar } from "./TitleBar";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import type { ActivityItem, ViewId } from "./views";
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

type Dialog = "settings" | "shortcuts" | "quick-setup" | "add-playlist";

/** The application layout: menu bar over the activity bar, sidebar and central area. */
export function Shell() {
  const [view, setView] = useState<ViewId>("workspace");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const { view: workspace } = useWorkspace();
  const { values, shortcuts, restart } = useSettings();
  // Settings arrive before the shell renders, so a first run is known here.
  const [dialog, setDialog] = useState<Dialog | null>(
    values["ui.quick_setup_done"] === false ? "quick-setup" : null,
  );
  // A late close of one dialog cannot dismiss another that has opened since.
  const closer = (which: Dialog) => () => setDialog((now) => (now === which ? null : now));
  const viewerActions = useRef<ViewerActions>({});

  const addVideo = () => {
    const bridge = window.axDevilWindow;
    if (!bridge) return setNotice("Adding a video needs the ax-devil desktop app");
    bridge
      .pickVideos()
      .then((paths) => (paths.length ? addVideos(paths) : null))
      .then(setNotice)
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : String(error)));
  };

  // Commands with behavior; every other command is a placeholder that does nothing yet. The open viewer adds its own.
  const actions: Record<string, () => void> = {
    sidebar: () => setSidebarOpen((open) => !open),
    settings: () => setDialog("settings"),
    "keyboard-shortcuts": () => setDialog("shortcuts"),
    "quick-setup": () => setDialog("quick-setup"),
    "add-video": addVideo,
    "add-playlist": () => setDialog("add-playlist"),
  };
  // A modal dialog owns the keyboard and the window: no command, from a shortcut or a menu, runs while one is open.
  const find = (id: string) => (dialog ? undefined : (actions[id] ?? viewerActions.current[id]));
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
    else if (sidebarOpen && view === item.id) setSidebarOpen(false);
    else {
      setView(item.id as ViewId);
      setSidebarOpen(true);
    }
  };

  useEffect(() => {
    /** The command this key press runs, if the shell handles it. */
    const handled = (event: KeyboardEvent) => {
      const pressed = shortcutOf(event);
      const match =
        pressed && COMMANDS.find((item) => shortcuts[item.id] === pressed && find(item.id));
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
    <ViewerActionsContext.Provider value={viewerActions}>
      <div className="flex h-screen flex-col">
        <h1 className="sr-only">ax-devil</h1>
        <TitleBar checked={checked} onSelect={select} />
        {notice && (
          <Banner role="alert" onDismiss={() => setNotice(null)}>
            {notice}
          </Banner>
        )}
        {restart.length > 0 && (
          <Banner role="status">
            Restart ax-devil to apply{" "}
            {restart.map((key) => SETTINGS.find((row) => row.key === key)?.label).join(", ")}
          </Banner>
        )}
        {workspace.connection === "lost" && (
          <Banner role="status">Connection to the backend lost. Reconnecting…</Banner>
        )}
        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <ActivityBar active={sidebarOpen ? view : null} onSelect={activate} />
          {sidebarOpen && <Sidebar view={view} onRun={run} />}
          <main
            className={`flex min-h-0 min-w-0 flex-1 overflow-auto ${sidebarOpen ? "max-sm:hidden" : ""}`}
          >
            {workspace.active ? <ViewerHost /> : <Welcome onRun={run} />}
          </main>
        </div>
        {dialog === "settings" && <SettingsDialog onClose={closer("settings")} />}
        {dialog === "quick-setup" && <QuickSetupDialog onClose={closer("quick-setup")} />}
        {dialog === "add-playlist" && <AddPlaylistDialog onClose={closer("add-playlist")} />}
        {dialog === "shortcuts" && <ShortcutsDialog onClose={closer("shortcuts")} />}
      </div>
    </ViewerActionsContext.Provider>
  );
}
