import { useEffect, useState } from "react";
import { useGrants } from "../access";
import { scopeOf } from "../commands";
import type { SidebarView } from "../generated/api";
import { COMMANDS, SETTINGS } from "../generated/settings";
import type { MenuCommand } from "../kit/Menu";
import { shortcutOf } from "../kit/shortcut";
import type { Scopes } from "../session/cursor";
import { Presence } from "../session/Presence";
import { useAllowed, useCommand, useView } from "../session/session";
import { useSettings } from "../settings";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import { useFrontViewer, useViewers } from "../viewer/useViewers";
import { ViewerHost } from "../viewer/ViewerHost";
import { ActivityBar } from "./ActivityBar";
import { AddVideoDialog } from "./AddVideoDialog";
import { Banner } from "./Banner";
import { TitleBar } from "./TitleBar";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import type { ActivityItem } from "./views";
import { useServerKind } from "./windowBridge";

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

type Dialog = "settings" | "shortcuts" | "quick-setup" | "add-video";

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
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const [viewerActions, setViewerActions] = useState<ViewerActions>(NO_ACTIONS);
  const { values, shortcuts, restart } = useSettings();
  // Quick Setup configures the server, so only someone who may configure it is offered it.
  const configures = useGrants().includes("configuration");
  // Settings arrive before the shell renders, so a first run is known here.
  const [dialog, setDialog] = useState<Dialog | null>(
    configures && values["ui.quick_setup_done"] === false ? "quick-setup" : null,
  );
  // A late close of one dialog cannot dismiss another that has opened since.
  const closer = (which: Dialog) => () => setDialog((now) => (now === which ? null : now));

  // Only the local backend's window may browse this machine's files; any other asks for a path on the server.
  const local = useServerKind() === "local";
  const addVideo = () => {
    const bridge = local ? window.axDevilWindow : undefined;
    if (!bridge) return setDialog("add-video");
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
    settings: () => setDialog("settings"),
    "keyboard-shortcuts": () => setDialog("shortcuts"),
    ...(configures && { "quick-setup": () => setDialog("quick-setup") }),
    "add-video": addVideo,
  };
  // A modal dialog owns the keyboard and the window: no command, from a shortcut or a menu, runs while one is open.
  const find = (id: string) =>
    dialog || !allowed[scopeOf(id)] ? undefined : (actions[id] ?? viewerActions[id]);
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
        {restart.length > 0 && (
          <Banner role="status">
            Restart ax-devil to apply{" "}
            {restart.map((key) => SETTINGS.find((row) => row.key === key)?.label).join(", ")}
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
        {dialog === "settings" && <SettingsDialog onClose={closer("settings")} />}
        {dialog === "quick-setup" && <QuickSetupDialog onClose={closer("quick-setup")} />}
        {dialog === "add-video" && <AddVideoDialog onClose={closer("add-video")} />}
        {dialog === "shortcuts" && <ShortcutsDialog onClose={closer("shortcuts")} />}
      </div>
    </ViewerActionsContext.Provider>
  );
}
