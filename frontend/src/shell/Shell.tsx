import { useEffect, useState } from "react";
import { useGrants } from "../access";
import { scopeOf } from "../commands";
import type { SidebarView, WorkspaceCommand } from "../generated/api";
import { COMMANDS } from "../generated/settings";
import { CommandFailed } from "../errors";
import { Button } from "../kit/Button";
import { ChoiceDialog } from "../kit/ChoiceDialog";
import type { MenuCommand } from "../kit/Menu";
import { shortcutOf } from "../kit/shortcut";
import type { Scopes } from "../session/cursor";
import { Presence } from "../session/Presence";
import { useBookmarksOnReplace } from "../bookmarks/viewerLink";
import { useAllowed, useCommand, useDoc, useView } from "../session/session";
import { useSettings } from "../settings";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import { useFrontViewer, useViewers } from "../viewer/useViewers";
import { ViewerHost } from "../viewer/ViewerHost";
import { AboutDialog } from "./AboutDialog";
import { ActivityBar } from "./ActivityBar";
import type { AddSeed } from "../live/cameras";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";
import { AddPlaylistDialog } from "./AddPlaylistDialog";
import { AddVideoDialog } from "./AddVideoDialog";
import { Banner } from "./Banner";
import { TitleBar } from "./TitleBar";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { RECENT_ID } from "./recents";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { KeptStateProvider } from "./keptState";
import { RevealLive, RevealShown } from "./RevealShown";
import { LISTED_VIEWS, Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import { WorkspaceFileDialog } from "./WorkspaceFileDialog";
import type { ActivityItem } from "./views";
import { toggleAppFullscreen, useServerKind } from "./windowBridge";

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

/** The View menu checks that are saved settings, by command. */
const SETTING_CHECKS = {
  "object-details": "overlays.object_details",
  "lane-names": "overlays.lane_names",
} as const;

type Dialog =
  | "settings"
  | "restart"
  | "shortcuts"
  | "quick-setup"
  | "about"
  | "add-video"
  | "add-live-stream"
  | "add-playlist"
  | "open-workspace"
  | "save-workspace-as";

/**
 * The application layout: menu bar over the activity bar, sidebar and central area. What the window shows comes from
 * the session's view, so a watcher that follows the presenter shows the presenter's. `version` is the backend's.
 */
export function Shell({ version }: { version: string }) {
  const [sidebar, setSidebar] = useView("sidebar");
  const { active } = useViewers();
  useFrontViewer();
  useBookmarksOnReplace();
  const allowed = useAllowed();
  const command = useCommand();
  const workspace = useDoc()?.workspace;
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  // The file a typed Save As would replace, until the user answers.
  const [replacing, setReplacing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // A failed save of a setting: a notice the user acknowledges, as the app's own dialogs do.
  const [refused, setRefused] = useState<{ title: string; message: string } | null>(null);
  const [viewerActions, setViewerActions] = useState<ViewerActions>(NO_ACTIONS);
  const { values, shortcuts, save } = useSettings();
  // Quick Setup configures the server, so only someone who may configure it is offered it.
  const configures = useGrants().includes("configuration");
  // Settings arrive before the shell renders, so a first run is known here.
  const [dialog, setDialog] = useState<Dialog | null>(
    configures && values["ui.quick_setup_done"] === false ? "quick-setup" : null,
  );
  // A late close of one dialog cannot dismiss another that has opened since.
  const [addSeed, setAddSeed] = useState<AddSeed>({});
  const closer = (which: Dialog) => () => setDialog((now) => (now === which ? null : now));

  // Only the local backend's window may browse this machine's files; any other asks for a path on the server.
  const local = useServerKind() === "local";
  const bridge = local ? window.axDevilWindow : undefined;
  const fail = (error: unknown) =>
    setNotice(error instanceof Error ? error.message : String(error));
  // A refused or failed workspace command shows why, as any other command does.
  const ask = (change: WorkspaceCommand) =>
    command({ type: "workspace", command: change }).then(() => setNotice(null), fail);
  const openWorkspace = () => {
    if (!bridge) return setDialog("open-workspace");
    bridge
      .pickOpenWorkspace()
      .then((path) => (path ? ask({ type: "open", path }) : undefined))
      .catch(fail);
  };
  const saveAs = () => {
    if (!bridge) return setDialog("save-workspace-as");
    bridge
      .pickSaveWorkspace(workspace?.name ?? "Untitled", workspace?.file ?? null)
      // The native dialog has asked about an existing file already.
      .then((path) => (path ? ask({ type: "save", path, replace: true }) : undefined))
      .catch(fail);
  };
  // A typed path is saved to unless it names another file that exists, which the user is asked about.
  const saveTo = (path: string) =>
    command({ type: "workspace", command: { type: "save", path } }).then(
      () => setNotice(null),
      (error: unknown) =>
        error instanceof CommandFailed && error.error.code === "WorkspaceExists"
          ? setReplacing(path)
          : fail(error),
    );
  const saveFile = () => (workspace?.file ? void ask({ type: "save" }) : saveAs());

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
    fullscreen: toggleAppFullscreen,
    // A browser tab cannot close itself.
    ...(window.axDevilWindow && { exit: () => window.axDevilWindow?.close() }),
    settings: () => setDialog("settings"),
    "keyboard-shortcuts": () => setDialog("shortcuts"),
    ...(configures && { "quick-setup": () => setDialog("quick-setup") }),
    about: () => setDialog("about"),
    "add-video": () => setDialog("add-video"),
    "new-workspace": () => void ask({ type: "new" }),
    "open-workspace": openWorkspace,
    "save-workspace": saveFile,
    "save-workspace-as": saveAs,
    "add-live-stream": () => setDialog("add-live-stream"),
    "add-playlist": () => setDialog("add-playlist"),
    ...Object.fromEntries(
      Object.entries(SETTING_CHECKS).map(([id, key]) => [
        id,
        () =>
          void save({ values: { ...values, [key]: values[key] !== true }, shortcuts }).then(
            (refusal) =>
              refusal &&
              setRefused({
                title: "Settings",
                message: `Could not save this preference: ${refusal.reason}`,
              }),
          ),
      ]),
    ),
  };
  // A modal dialog owns the keyboard and the window: no command, from a shortcut or a menu, runs while one is open.
  const find = (id: string) =>
    dialog || replacing || refused || !allowed[scopeOf(id)]
      ? undefined
      : (actions[id] ?? viewerActions[id]);
  const run = (id: string) => {
    if (!id.startsWith(RECENT_ID)) return find(id)?.();
    if (!dialog && !replacing && allowed.host)
      void ask({ type: "open", path: id.slice(RECENT_ID.length) });
  };
  // The sidebar's Add opens the dialog from a seed; the menu and shortcut open it without one.
  const addLive = (seed: AddSeed) => {
    if (!find("add-live-stream")) return;
    setAddSeed(seed);
    setDialog("add-live-stream");
  };
  const select = (item: MenuCommand) => {
    if (item.kind === "check" && !(item.id in SETTING_CHECKS))
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
      // A modal dialog the shell did not open (the uncaught-error dialog) owns the keyboard too.
      if (document.querySelector("dialog[open]")) return undefined;
      const pressed = shortcutOf(event);
      const match =
        pressed && COMMANDS.find((item) => shortcuts[item.id] === pressed && find(item.id));
      const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
      return match && !(plain && !viewerOwnsPlainKeys(event.target)) ? match : undefined;
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const match = handled(event);
      if (!match) {
        // The start panel's default button: Enter with nothing focused runs Open Workspace, once no shortcut has taken
        // the key, no dialog is open and no viewer covers the welcome area (a focused button keeps its own Enter).
        if (
          event.key === "Enter" &&
          !event.ctrlKey &&
          !event.altKey &&
          !event.metaKey &&
          event.target === document.body &&
          sidebar.open &&
          sidebar.view === "workspace" &&
          !active &&
          !document.querySelector("dialog[open]")
        ) {
          event.preventDefault();
          run("open-workspace");
        }
        return;
      }
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
    <KeptStateProvider>
      {LISTED_VIEWS.map((view) => (
        <RevealShown key={view} view={view} />
      ))}
      <RevealLive />
      <ViewerActionsContext.Provider value={setViewerActions}>
        <div className="flex min-h-0 flex-1 flex-col">
          <h1 className="sr-only">ax-devil</h1>
          <TitleBar
            checked={
              new Set([
                ...checked,
                ...Object.entries(SETTING_CHECKS)
                  .filter(([, key]) => values[key] === true)
                  .map(([id]) => id),
              ])
            }
            wired={new Set([...Object.keys(actions), ...Object.keys(viewerActions)])}
            onSelect={select}
            onStartSharing={startSharing}
          />
          {notice && (
            <Banner role="alert" onDismiss={() => setNotice(null)}>
              {notice}
            </Banner>
          )}
          {workspace?.recovered && (
            <Banner
              role="status"
              actions={
                <>
                  <Button
                    size="small"
                    disabled={!allowed.content}
                    onClick={() => void ask({ type: "keep" })}
                  >
                    Keep
                  </Button>
                  {allowed.host && (
                    <Button size="small" onClick={() => void ask({ type: "discard" })}>
                      Discard
                    </Button>
                  )}
                </>
              }
            >
              Restored unsaved changes
            </Banner>
          )}
          <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
            <ActivityBar active={sidebar.open ? sidebar.view : null} onSelect={activate} />
            {sidebar.open && <Sidebar view={sidebar.view} onRun={run} onAddLive={addLive} />}
            <main
              data-cursor-region="main"
              className={`flex min-h-0 min-w-0 flex-1 overflow-auto ${sidebar.open ? "max-sm:hidden" : ""}`}
            >
              {active ? <ViewerHost /> : <Welcome onRun={run} />}
            </main>
          </div>
          <Presence scopes={scopes} />
          {dialog === "settings" && (
            <SettingsDialog
              onClose={closer("settings")}
              onRestartNeeded={() => setDialog("restart")}
            />
          )}
          {dialog === "restart" && (
            <ChoiceDialog
              title="Restart ax-devil?"
              message="Some changes take effect after a restart."
              // Only a backend the desktop started itself is restarted with it.
              choices={
                bridge?.restart
                  ? [
                      { id: "now", label: "Restart Now" },
                      { id: "later", label: "Later" },
                    ]
                  : [{ id: "later", label: "OK" }]
              }
              defaultId={bridge?.restart ? "now" : "later"}
              onChoose={(choice) => {
                closer("restart")();
                if (choice === "now") bridge?.restart?.();
              }}
            />
          )}
          {dialog === "quick-setup" && (
            <QuickSetupDialog
              onClose={closer("quick-setup")}
              onNotice={(message) => setRefused({ title: "Quick Setup", message })}
            />
          )}
          {dialog === "about" && <AboutDialog version={version} onClose={closer("about")} />}
          {dialog === "add-video" && <AddVideoDialog onClose={closer("add-video")} />}
          {dialog === "add-live-stream" && (
            <AddLiveStreamDialog
              seed={addSeed}
              onClose={() => {
                setAddSeed({});
                closer("add-live-stream")();
              }}
            />
          )}
          {dialog === "add-playlist" && <AddPlaylistDialog onClose={closer("add-playlist")} />}
          {dialog === "open-workspace" && (
            <WorkspaceFileDialog
              mode="open"
              onSubmit={(path) => void ask({ type: "open", path })}
              onClose={closer("open-workspace")}
            />
          )}
          {dialog === "save-workspace-as" && (
            <WorkspaceFileDialog
              mode="save"
              path={workspace?.file ?? ""}
              onSubmit={(path) => void saveTo(path)}
              onClose={closer("save-workspace-as")}
            />
          )}
          {replacing && (
            <ChoiceDialog
              title="Save workspace as"
              message={`Replace ${replacing.split(/[/\\]/).pop()}?`}
              choices={[
                { id: "replace", label: "Replace" },
                { id: "cancel", label: "Cancel" },
              ]}
              defaultId="cancel"
              onChoose={(choice) => {
                setReplacing(null);
                if (choice === "replace")
                  void ask({ type: "save", path: replacing, replace: true });
              }}
            />
          )}
          {dialog === "shortcuts" && <ShortcutsDialog onClose={closer("shortcuts")} />}
          {refused && (
            <ChoiceDialog
              title={refused.title}
              message={refused.message}
              choices={[{ id: "ok", label: "OK" }]}
              defaultId="ok"
              onChoose={() => setRefused(null)}
            />
          )}
        </div>
      </ViewerActionsContext.Provider>
    </KeptStateProvider>
  );
}
