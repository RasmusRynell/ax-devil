import { splitPath } from "../../../shared/file-name.mjs";
import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { useAccess } from "./access";
import { ask } from "./ask";
import { reasonOf } from "./errors";
import type { FolderListing } from "../generated/api";
import { FolderProblem, PathPicker, type CheckExists } from "../kit/PathPicker";
import { SUFFIX } from "./recents";
import { type WindowBridge } from "./windowBridge";

/** What to choose: how the shell's own dialog does it, and how the path picker does it where there is none. */
export interface PathRequest {
  native: (bridge: WindowBridge) => Promise<string | null> | undefined;
  picker: Omit<Parameters<typeof PathPicker>[0], "list" | "exists" | "onChoose" | "onCancel">;
}

/** One video file. */
export const VIDEO: PathRequest = {
  native: (bridge) => bridge.pickVideo?.(),
  picker: { mode: "file", title: "Choose video" },
};

/** One file of one of `extensions` (each lowercase with its dot; none lists every file). */
export const chooseFile = (extensions: readonly string[]): PathRequest => ({
  native: (bridge) => bridge.pickOverlay?.(extensions),
  picker: { mode: "file", title: "Choose file", extensions },
});

/** A folder; `title` names what it is for. */
export const chooseFolder = (title?: string): PathRequest => ({
  native: (bridge) => bridge.pickFolder?.(title),
  picker: { mode: "folder", title: title ?? "Choose folder" },
});

/** A saved workspace to open. */
export const OPEN_WORKSPACE: PathRequest = {
  native: (bridge) => bridge.pickOpenWorkspace?.(),
  picker: { mode: "file", title: "Open workspace", extensions: [SUFFIX] },
};

/** Where to save a workspace called `name`, which starts at `current` when it already has a file. */
export const saveWorkspace = (name: string, current: string | null): PathRequest => ({
  native: (bridge) => bridge.pickSaveWorkspace?.(name, current),
  picker: {
    mode: "save",
    title: "Save workspace as",
    extensions: [SUFFIX],
    start: current ? splitPath(current).folder : undefined,
    name: current ? splitPath(current).name : name,
  },
});

/** Where to write an export, suggested as `name` and ending in `.extension`. */
export const exportFile = (
  name: string,
  extension: "mp4" | "png" | "csv",
  folder?: string,
): PathRequest => ({
  native: (bridge) => bridge.pickExportFile?.(name, extension, folder),
  picker: {
    mode: "save",
    start: folder,
    title: extension === "csv" ? "Export CSV" : "Export video",
    extensions: [`.${extension}`],
    name,
  },
});

/** Lists a folder of the backend's machine, or where to start when `path` is absent. */
async function listFolder(path: string | undefined, signal: AbortSignal): Promise<FolderListing> {
  const answer = await ask("GET /api/files/folder", { request: { path }, signal });
  if (answer.ok) return answer.value;
  throw new FolderProblem(reasonOf(answer.error), answer.error.code === "InvalidPath");
}

/** Whether the backend's machine has anything at `path`, hidden files and other-case names included. */
const pathExists: CheckExists = async (path) => {
  const answer = await ask("GET /api/files/exists", { request: { path } });
  if (answer.ok) return answer.value.exists;
  throw new Error(reasonOf(answer.error));
};

/** Resolves to the absolute path chosen on the backend's machine; null when cancelled. */
export type PickPath = (request: PathRequest) => Promise<string | null>;

const PickContext = createContext<PickPath | undefined>(undefined);

interface Asking {
  request: PathRequest;
  settle: (path: string | null) => void;
}

/** Serves the path picker to an Owner, for the windows that have no file dialog of their own. */
export function PathPickerProvider({ children }: { children: ReactNode }) {
  const { owner } = useAccess();
  const [asking, setAsking] = useState<Asking | null>(null);
  const opener = useRef<Element | null>(null);
  const pick: PickPath = (request) =>
    new Promise((resolve) => {
      opener.current = document.activeElement;
      setAsking((now) => {
        now?.settle(null);
        return { request, settle: resolve };
      });
    });
  const done = (path: string | null) => {
    asking?.settle(path);
    setAsking(null);
    // The picker's dialog is gone with this render; the control that opened it takes the focus back.
    queueMicrotask(() => (opener.current as HTMLElement | null)?.focus());
  };
  return (
    <PickContext.Provider value={owner ? pick : undefined}>
      {children}
      {asking && (
        // The window's shortcuts do not run while the picker is open.
        <div className="contents" onKeyDown={(event) => event.stopPropagation()}>
          <PathPicker
            {...asking.request.picker}
            list={listFolder}
            exists={pathExists}
            onChoose={done}
            onCancel={() => done(null)}
          />
        </div>
      )}
    </PickContext.Provider>
  );
}

/**
 * The one way to let the user choose a path on the backend's machine: the shell's own dialog where this window has
 * one, else the path picker served from the backend. Undefined where neither is offered, such as for a Guest, so a
 * control that needs it is not shown.
 */
export function usePickPath(): PickPath | undefined {
  const served = useContext(PickContext);
  const bridge = window.axDevilWindow;
  // The shell offers its file dialogs all or none, and only for the windows on this machine's own backend.
  return bridge && Object.keys(bridge).some((name) => name.startsWith("pick"))
    ? (request) => request.native(bridge) ?? Promise.resolve(null)
    : served;
}
