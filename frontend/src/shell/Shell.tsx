import { useEffect, useState } from "react";
import { useAccess } from "../client/access";
import { scopeOf } from "../client/commands";
import type { WorkspaceCommand } from "../generated/api";
import { COMMANDS } from "../generated/settings";
import { reasonOf } from "../client/errors";
import { Button } from "../kit/Button";
import { ChoiceDialog } from "../kit/ChoiceDialog";
import type { MenuCommand } from "../kit/Menu";
import { shortcutOf } from "../kit/shortcut";
import type { Scopes } from "../session/cursor";
import { Presence } from "../session/Presence";
import { useSidebarOnReplace } from "../bookmarks/viewerLink";
import { useAllowed, useCommand, useDoc, useOverlays, useView } from "../session/session";
import { useSettings } from "../client/settings";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import { MAX_PANES } from "../session/views";
import { useFrontViewer, useViewers } from "../viewer/useViewers";
import { SplitArea } from "./SplitArea";
import { AboutDialog } from "./AboutDialog";
import { ActivityBar } from "./ActivityBar";
import type { AddSeed } from "../live/cameras";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";
import { AddPlaylistDialog } from "./AddPlaylistDialog";
import { AddVideoDialog } from "./AddVideoDialog";
import { Banner } from "../kit/Banner";
import { TitleBar } from "./TitleBar";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { RECENT_ID } from "../client/recents";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { KeptStateProvider } from "../kit/keptState";
import { useRunPlugin } from "../plugins/RunDialog";
import {
  PluginViewsProvider,
  RunCommandContext,
  usePluginCommands,
  type RunAt,
} from "../plugins/usePlugins";
import { RevealLive, RevealShown } from "./RevealShown";
import { LISTED_VIEWS, Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import { viewId, viewOf, type ActivityItem } from "./views";
import { toggleAppFullscreen, useAppFullscreen } from "../client/windowBridge";
import { OPEN_WORKSPACE, saveWorkspace, usePickPath } from "../client/pickPath";

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

/** The View menu checks that are overlays of the Session's view, by command, and the setting each starts a Session from. */
const OVERLAY_CHECKS = {
  "object-details": { overlay: "objectDetails", setting: "overlays.object_details" },
  "lane-names": { overlay: "laneNames", setting: "overlays.lane_names" },
} as const;

type Dialog =
  | "settings"
  | "restart"
  | "shortcuts"
  | "quick-setup"
  | "about"
  | "add-video"
  | "add-live-stream"
  | "add-playlist";

/** The application layout, with the plugins' views and commands listed for it. */
export function Shell({ version }: { version: string }) {
  return (
    <PluginViewsProvider>
      <ShellBody version={version} />
    </PluginViewsProvider>
  );
}

/**
 * The application layout: menu bar over the activity bar, sidebar and central area. What the window shows comes from
 * the session's view, so a Guest that follows the presenter shows the presenter's. `version` is the backend's.
 */
function ShellBody({ version }: { version: string }) {
  const [sidebar, setSidebar] = useView("sidebar");
  const fullscreen = useAppFullscreen();
  const { viewers, active } = useViewers();
  useSidebarOnReplace();
  const allowed = useAllowed();
  const command = useCommand();
  const workspace = useDoc()?.workspace;
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  useFrontViewer(() => setNotice(`Cap at ${MAX_PANES} panes`));
  // A failed save of a setting: a notice the user acknowledges, as the app's own dialogs do.
  const [refused, setRefused] = useState<{ title: string; message: string } | null>(null);
  const [viewerActions, setViewerActions] = useState<ViewerActions>(NO_ACTIONS);
  const { values, shortcuts, set } = useSettings();
  const overlays = useOverlays();
  const { owner } = useAccess();
  // Settings arrive before the shell renders, so a first run is known here.
  const [dialog, setDialog] = useState<Dialog | null>(
    owner && values["ui.quick_setup_done"] === false ? "quick-setup" : null,
  );
  // A late close of one dialog cannot dismiss another that has opened since.
  const [addSeed, setAddSeed] = useState<AddSeed>({});
  const closer = (which: Dialog) => () => setDialog((now) => (now === which ? null : now));

  const bridge = window.axDevilWindow;
  const pick = usePickPath();
  const fail = (error: unknown) =>
    setNotice(error instanceof Error ? error.message : String(error));
  // A refused or failed workspace command shows why, as any other command does.
  const ask = (change: WorkspaceCommand) =>
    command({ type: "workspace", command: change }).then(() => setNotice(null), fail);
  const openWorkspace = () =>
    void pick?.(OPEN_WORKSPACE)
      .then((path) => (path ? ask({ type: "open", path }) : undefined))
      .catch(fail);
  const saveAs = () =>
    void pick?.(saveWorkspace(workspace?.name ?? "Untitled", workspace?.file ?? null))
      // Either dialog has asked about an existing file already.
      .then((path) => (path ? ask({ type: "save", path, replace: true }) : undefined))
      .catch(fail);
  const saveFile = () => (workspace?.file ? void ask({ type: "save" }) : saveAs());

  const startSharing = () => {
    setSidebar({ view: "sessions", open: true });
    command({ type: "control", control: { type: "share", access: "watch" } }).then(
      () => setNotice(null),
      (error: unknown) => setNotice(error instanceof Error ? error.message : String(error)),
    );
  };

  // Commands with behavior; every other command is a placeholder that does nothing yet. The open viewer adds its own.
  const plugins = usePluginCommands();
  const runner = useRunPlugin(plugins, {
    front: active?.id,
    reveal: (view) => setSidebar({ view: { plugin: { view } }, open: true }),
    notify: setNotice,
  });
  const actions: Record<string, (at?: RunAt) => void> = {
    ...Object.fromEntries(plugins.map(({ id }) => [id, (at?: RunAt) => runner.run(id, at)])),
    sidebar: () => setSidebar({ ...sidebar, open: !sidebar.open }),
    fullscreen: toggleAppFullscreen,
    // A browser tab cannot close itself.
    ...(window.axDevilWindow && { exit: () => window.axDevilWindow?.close() }),
    settings: () => setDialog("settings"),
    plugins: () => setSidebar({ view: "plugins", open: true }),
    ...(owner && { "keyboard-shortcuts": () => setDialog("shortcuts") }),
    ...(owner && { "quick-setup": () => setDialog("quick-setup") }),
    about: () => setDialog("about"),
    "add-video": () => setDialog("add-video"),
    "new-workspace": () => void ask({ type: "new" }),
    "open-workspace": openWorkspace,
    "save-workspace": saveFile,
    "save-workspace-as": saveAs,
    "add-live-stream": () => setDialog("add-live-stream"),
    "add-playlist": () => setDialog("add-playlist"),
    // The Session's view takes the change; an Owner's choice also starts the next Session the same way.
    ...(allowed.view &&
      Object.fromEntries(
        Object.entries(OVERLAY_CHECKS).map(([id, { overlay, setting }]) => [
          id,
          () => {
            const shown = !overlays[overlay];
            command({ type: "setOverlay", overlay, shown }).catch(fail);
            if (owner)
              void set(setting, shown).then(
                (refusal) =>
                  refusal &&
                  setRefused({
                    title: "Settings",
                    message: `Could not save this preference: ${reasonOf(refusal)}`,
                  }),
              );
          },
        ]),
      )),
  };
  // A modal dialog owns the keyboard and the window: no command, from a shortcut or a menu, runs while one is open.
  const find = (id: string): ((at?: RunAt) => void) | undefined =>
    dialog || refused || runner.running || !allowed[scopeOf(id)]
      ? undefined
      : (actions[id] ?? viewerActions[id]);
  const run = (id: string, at?: RunAt) => {
    if (!id.startsWith(RECENT_ID)) return find(id)?.(at);
    if (!dialog && allowed.host) void ask({ type: "open", path: id.slice(RECENT_ID.length) });
  };
  // The sidebar's Add opens the dialog from a seed; the menu and shortcut open it without one.
  const addLive = (seed: AddSeed) => {
    if (!find("add-live-stream")) return;
    setAddSeed(seed);
    setDialog("add-live-stream");
  };
  const select = (item: MenuCommand) => {
    // A check with no command behind it only flips its mark.
    if (item.kind === "check" && !(item.id in actions) && !(item.id in viewerActions))
      setChecked((now) => {
        const next = new Set(now);
        if (!next.delete(item.id)) next.add(item.id);
        return next;
      });
    else run(item.id);
  };
  const activate = (item: ActivityItem) => {
    if (item.opens === "command") run(item.id);
    else if (sidebar.open && viewId(sidebar.view) === item.id)
      setSidebar({ ...sidebar, open: false });
    else setSidebar({ view: viewOf(item), open: true });
  };
  // What each region of this window shows, so a cursor lands by region only where the same thing is shown.
  const scopes: Scopes = { sidebar: viewId(sidebar.view), main: active?.item.id ?? "welcome" };

  useEffect(() => {
    /** The command this key press runs, if the shell handles it. */
    const handled = (event: KeyboardEvent) => {
      // A modal dialog the shell did not open (the uncaught-error dialog) owns the keyboard too.
      if (document.querySelector("dialog[open]")) return undefined;
      const pressed = shortcutOf(event);
      const match =
        pressed &&
        [...COMMANDS, ...plugins.map(({ id }) => ({ id, repeats: false }))].find(
          (item) => shortcuts[item.id] === pressed && find(item.id),
        );
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
          viewers.length === 0 &&
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
      <RunCommandContext value={run}>
        <ViewerActionsContext.Provider value={setViewerActions}>
          <div className="flex min-h-0 flex-1 flex-col">
            <h1 className="sr-only">ax-devil</h1>
            <TitleBar
              checked={
                new Set([
                  ...checked,
                  ...(fullscreen ? ["fullscreen"] : []),
                  ...(sidebar.open ? ["sidebar"] : []),
                  ...Object.entries(OVERLAY_CHECKS)
                    .filter(([, { overlay }]) => overlays[overlay])
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
              <ActivityBar
                active={sidebar.open ? viewId(sidebar.view) : null}
                onSelect={activate}
              />
              {sidebar.open && <Sidebar view={sidebar.view} onRun={run} onAddLive={addLive} />}
              <main
                data-cursor-region="main"
                className={`flex min-h-0 min-w-0 flex-1 overflow-auto ${sidebar.open ? "max-sm:hidden" : ""}`}
              >
                {viewers.length > 0 ? <SplitArea /> : <Welcome onRun={run} />}
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
            {runner.dialog}
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
      </RunCommandContext>
    </KeptStateProvider>
  );
}
