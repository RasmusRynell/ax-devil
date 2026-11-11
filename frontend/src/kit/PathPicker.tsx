import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { FolderListing, FolderPlace } from "../generated/api";
import { Button } from "./Button";
import { Chip, ChipRow } from "./Chips";
import { Dialog, DialogBody } from "./Dialog";
import { TextField } from "./Fields";
import { Icon } from "./Icon";
import { ownKeys } from "./keys";
import { Refusal } from "./Refusal";
import { useRowWindow } from "./useRowWindow";

/** What a path picker chooses: a folder, an existing file, or a file name to write in a folder. */
export type PickMode = "folder" | "file" | "save";

/** Why a folder could not be listed; `notAFolder` when the path names something that is not a folder. */
export class FolderProblem extends Error {
  constructor(
    reason: string,
    readonly notAFolder: boolean,
  ) {
    super(reason);
  }
}

/** Lists a folder of the backend's machine, or answers where to start when `path` is absent; rejects with a `FolderProblem`. */
export type ListFolder = (path: string | undefined, signal: AbortSignal) => Promise<FolderListing>;

/** Whether anything, hidden or named in another case, is at `path` on the backend's machine; rejects with an `Error`. */
export type CheckExists = (path: string) => Promise<boolean>;

interface Props {
  mode: PickMode;
  list: ListFolder;
  /** Asked before `save` returns a path, which the user then agreed to replace when something is there. */
  exists: CheckExists;
  title: string;
  /** Where it opens: a folder, or for `save` the path of the file it offers to write. */
  start?: string;
  /** `file` lists only files with these extensions (lowercase, with their dots; none lists every file); `save` ends the name in the first. */
  extensions?: readonly string[];
  /** The file name `save` offers when `start` gives none. */
  name?: string;
  onChoose: (path: string) => void;
  onCancel: () => void;
}

const listKeys = ownKeys(new Set(["ArrowUp", "ArrowDown", "Home", "End", "Enter"]));

/** The folder and the last name of `path`, splitting at the last separator. */
function split(path: string): [string, string] {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return [path.slice(0, cut + 1), path.slice(cut + 1)];
}

/** `name` inside `folder`, with the separator the folder's own path uses. */
function join(folder: string, name: string): string {
  const separator = folder.includes("\\") && !folder.includes("/") ? "\\" : "/";
  return folder.endsWith(separator) ? `${folder}${name}` : `${folder}${separator}${name}`;
}

/**
 * Browses the backend machine's folders to choose a path on it, for a window that has no file dialog of its own.
 * The path can also be typed or pasted; a path of a file takes its folder and name.
 */
export function PathPicker({
  mode,
  list,
  exists,
  title,
  start,
  extensions = [],
  name = "",
  onChoose,
  onCancel,
}: Props) {
  const [first, base] =
    start === undefined ? ["", name] : mode === "save" ? split(start) : [start, ""];
  const [folder, setFolder] = useState<string | undefined>(first || undefined);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [places, setPlaces] = useState<readonly FolderPlace[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(first);
  const [active, setActive] = useState(0);
  const [fileName, setFileName] = useState(base || name);
  // The name a typed file path ended in, which the folder it opens in then selects.
  const wanted = useRef<string | null>(null);
  // The existing file the user was asked about; asking again returns it.
  const [replacing, setReplacing] = useState<string | null>(null);
  // The path whose existence is being asked; an answer for any other is stale.
  const asked = useRef<string | null>(null);
  const [asking, setAsking] = useState(false);
  const id = useId();
  const nameField = useRef<HTMLDivElement>(null);

  // The places are asked once, as the picker opens, and stay for every folder; with no folder to open, the first starts it.
  useEffect(() => {
    const request = new AbortController();
    list(undefined, request.signal).then(
      (found) => {
        setPlaces(found.places);
        setFolder((current) => current ?? found.places[0]?.path);
      },
      (failure: unknown) => {
        if (!request.signal.aborted)
          setError(failure instanceof Error ? failure.message : String(failure));
      },
    );
    return () => request.abort();
  }, [list]);

  useEffect(() => {
    if (folder === undefined) return;
    const request = new AbortController();
    list(folder, request.signal).then(
      (found) => {
        if (found.path === undefined) return;
        setListing(found);
        setText(found.path);
        setError(null);
      },
      (failure: unknown) => {
        if (request.signal.aborted) return;
        // A path that is a file, not a folder, or in save mode one not there yet: choose it by
        // opening its folder with its name ready.
        const [dir, file] = split(folder ?? "");
        const notAFolder = failure instanceof FolderProblem && failure.notAFolder;
        if ((notAFolder ? mode !== "folder" : mode === "save") && dir && file) {
          wanted.current = file;
          setFolder(dir);
          return;
        }
        // No listing is left to act on while the error shows.
        setListing(null);
        setError(failure instanceof Error ? failure.message : String(failure));
      },
    );
    return () => request.abort();
  }, [folder, mode, list]);

  const wanting = (entry: { name: string }) =>
    extensions.length === 0 || extensions.some((one) => entry.name.toLowerCase().endsWith(one));
  const shown = (listing?.entries ?? []).filter(
    (entry) => entry.folder || (mode !== "folder" && wanting(entry)),
  );
  const rows = useRowWindow(shown.length, '[role="option"]');
  const extension = extensions[0] ?? "";
  const named = (file: string) =>
    extension && !file.toLowerCase().endsWith(extension) ? `${file}${extension}` : file;

  const forget = () => {
    asked.current = null;
    setAsking(false);
  };

  // A new listing starts on the file a typed path named, else on its first row.
  useEffect(() => {
    const at = shown.findIndex((entry) => entry.name === wanted.current);
    if (mode === "save" && wanted.current !== null) setFileName(wanted.current);
    if (at >= 0) {
      setActive(at);
      rows.reveal(at);
    } else setActive(0);
    wanted.current = null;
    setReplacing(null);
    forget();
    // `shown` follows the listing; only a new listing resets the row.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing]);
  // The dialog focuses its first control once the page has it; the list, or the name to write, takes over.
  useEffect(() => {
    queueMicrotask(() =>
      (mode === "save" ? nameField.current?.querySelector("input") : rows.ref.current)?.focus(),
    );
    // Once, as the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choose = (file: string) => listing?.path && onChoose(join(listing.path, file));
  const open = (index: number) => {
    const entry = shown[index];
    if (!entry || !listing?.path) return;
    if (entry.folder) setFolder(join(listing.path, entry.name));
    else if (mode === "save") {
      setFileName(entry.name);
      void write(named(entry.name));
    } else choose(entry.name);
  };
  // Writing over a file asks first, in the Save button, which then reads Replace; the server says whether it is there.
  const write = async (file: string) => {
    if (!listing?.path || asked.current) return;
    const target = join(listing.path, file);
    if (replacing === file) return onChoose(target);
    asked.current = target;
    setAsking(true);
    setError(null);
    let there: boolean;
    try {
      there = await exists(target);
    } catch (failure) {
      if (asked.current !== target) return;
      forget();
      return setError(failure instanceof Error ? failure.message : String(failure));
    }
    if (asked.current !== target) return;
    forget();
    if (there) setReplacing(file);
    else onChoose(target);
  };
  const move = (to: number) => {
    const next = Math.min(Math.max(to, 0), shown.length - 1);
    setActive(next);
    rows.reveal(next);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    listKeys(event);
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === "ArrowDown") move(active + 1);
    else if (event.key === "ArrowUp") move(active - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(shown.length - 1);
    else if (event.key === "Enter") open(active);
    else return;
    event.preventDefault();
  };

  const current = shown[active];
  const ready =
    listing?.path !== undefined &&
    (mode === "folder" || (mode === "file" ? current && !current.folder : fileName.trim() !== ""));
  const ok = () => {
    if (!listing?.path) return;
    if (mode === "folder") onChoose(listing.path);
    else if (mode === "save") void write(named(fileName.trim()));
    else open(active);
  };

  return (
    <Dialog
      title={title}
      okLabel={
        mode === "folder" || mode === "file"
          ? "Choose"
          : replacing === named(fileName.trim())
            ? "Replace"
            : "Save"
      }
      onOk={ok}
      onCancel={onCancel}
      busy={!ready || asking}
    >
      <DialogBody>
        <div className="flex items-center gap-s">
          <Button
            icon="chevron-up"
            label="Up"
            disabled={!listing?.parent}
            onClick={() => listing?.parent && setFolder(listing.parent)}
          />
          <div className="min-w-0 flex-1">
            <TextField
              fill
              label={<span className="sr-only">Path</span>}
              value={text}
              onChange={setText}
              onEnter={() => setFolder(text.trim())}
            />
          </div>
        </div>
        <ChipRow label="Places">
          {places.map((place) => (
            <Chip key={place.path} onClick={() => setFolder(place.path)} title={place.path}>
              {place.name}
            </Chip>
          ))}
        </ChipRow>
        {error && <Refusal reason={error} />}
        <div
          ref={rows.ref}
          role="listbox"
          aria-label="Entries"
          aria-activedescendant={shown[active] ? `${id}-${active}` : undefined}
          tabIndex={0}
          onKeyDown={onKeyDown}
          className="h-[calc(var(--spacing-row)*10)] min-h-list-least overflow-y-auto rounded-control border border-line-strong focus:border-accent focus:outline-none"
        >
          <div style={{ height: `calc(var(--spacing-row) * ${rows.start})` }} />
          {shown.slice(rows.start, rows.end).map((entry, offset) => {
            const index = rows.start + offset;
            return (
              <div
                key={entry.name}
                id={`${id}-${index}`}
                role="option"
                aria-selected={index === active}
                onClick={() => {
                  setActive(index);
                  if (mode === "save" && !entry.folder) setFileName(entry.name);
                }}
                onDoubleClick={() => open(index)}
                className="flex h-row cursor-pointer items-center gap-s border-l-edge border-transparent px-s hover:bg-hover aria-selected:border-accent aria-selected:bg-hover"
              >
                <Icon name={entry.folder ? "folder" : "file-text"} className="text-muted" />
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              </div>
            );
          })}
          <div style={{ height: `calc(var(--spacing-row) * ${shown.length - rows.end})` }} />
        </div>
        {mode === "save" && (
          <div ref={nameField}>
            <TextField
              label="Name"
              value={fileName}
              onChange={(value) => {
                forget();
                setFileName(value);
              }}
            />
          </div>
        )}
      </DialogBody>
    </Dialog>
  );
}
