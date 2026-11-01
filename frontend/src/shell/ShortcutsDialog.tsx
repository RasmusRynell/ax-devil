import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { Shortcuts } from "../commands";
import { COMMANDS, RESERVED, type CommandId } from "../generated/settings";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";
import { IconButton } from "../kit/IconButton";
import { KeyChips } from "../kit/KeyChips";
import { shortcutOf } from "../kit/shortcut";
import { useSettings } from "../settings";
import { useSaveDialog } from "./useSaveDialog";

/** Why a row's key was not taken, and, when another command holds it, the way to take it from that command. */
interface Note {
  id: CommandId;
  text: string;
  takeFrom?: { owner: CommandId; key: string };
}

const labelOf = (id: CommandId) => COMMANDS.find((command) => command.id === id)?.label ?? id;

/** The Keyboard Shortcuts dialog. Changes apply on OK, through the backend, which keeps only the differences. */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const { values, shortcuts } = useSettings();
  const [draft, setDraft] = useState<Shortcuts>(shortcuts);
  const [recording, setRecording] = useState<CommandId | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  // What the one polite live region says: recording started, a key set, cleared or reset.
  const [announcement, setAnnouncement] = useState("");
  const bindingId = useId();
  const { saving, refusal, submit } = useSaveDialog(onClose);
  // The release of a recorded key must not click the button it was recorded on.
  const swallowKeyUp = useRef(false);
  // The browser would focus the scrolling list itself; start on the first key instead.
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => list.current?.querySelector("button")?.focus(), []);

  /** Binds `key` (or unbinds with null) unless it is reserved or another command holds it. */
  const assign = (id: CommandId, key: string | null, reset = false) => {
    setRecording(null);
    const owner = key ? COMMANDS.find((other) => other.id !== id && draft[other.id] === key) : null;
    if (key && (RESERVED as readonly string[]).includes(key))
      setNote({ id, text: `${key} is reserved for zooming the page` });
    else if (key && owner)
      setNote({
        id,
        text: `${key} is already used by ${owner.label}`,
        takeFrom: { owner: owner.id, key },
      });
    else {
      setNote(null);
      setDraft({ ...draft, [id]: key });
      const verb = reset ? "reset to" : "set to";
      setAnnouncement(
        key
          ? `${labelOf(id)} ${verb} ${key}`
          : `${labelOf(id)} ${reset ? "reset to None" : "cleared"}`,
      );
    }
  };
  /** Binds `key` to `id` and unbinds the command that held it. */
  const reassign = (id: CommandId, { owner, key }: NonNullable<Note["takeFrom"]>) => {
    setDraft({ ...draft, [owner]: null, [id]: key });
    setNote(null);
    setAnnouncement(`${labelOf(id)} set to ${key}, taken from ${labelOf(owner)}`);
  };
  const record = (event: KeyboardEvent, id: CommandId) => {
    if (recording !== id) return;
    event.preventDefault();
    event.stopPropagation();
    swallowKeyUp.current = true;
    const key = shortcutOf(event.nativeEvent);
    if (event.key === "Escape") setRecording(null);
    else if (key) assign(id, key);
  };
  const serverNote = COMMANDS.find((row) => refusal?.location === `shortcuts.${row.id}`);

  return (
    <Dialog
      title="Keyboard Shortcuts"
      onOk={() => void submit({ values, shortcuts: draft })}
      onCancel={onClose}
      busy={saving}
    >
      <ul ref={list} className="m-0 flex min-h-0 list-none flex-col overflow-y-auto p-xs">
        {COMMANDS.map(({ id, label, key: standard }) => {
          const shown = note?.id === id ? note : null;
          const takeFrom = shown?.takeFrom;
          const text = shown?.text ?? (serverNote?.id === id ? refusal?.reason : null);
          return (
            <li key={id} className="flex flex-col">
              <div className="flex min-h-row items-center gap-m">
                <span className="min-w-0 flex-1">{label}</span>
                <button
                  type="button"
                  aria-label={`Key for ${label}`}
                  aria-describedby={`${bindingId}-${id}`}
                  onClick={() => {
                    setNote(null);
                    setRecording(id);
                    setAnnouncement(`Press a key for ${label}`);
                  }}
                  onKeyDown={(event) => record(event, id)}
                  onKeyUp={(event) => {
                    if (swallowKeyUp.current) event.preventDefault();
                    swallowKeyUp.current = false;
                  }}
                  onBlur={() => setRecording((now) => (now === id ? null : now))}
                  className="flex min-h-row items-center justify-end rounded-control bg-transparent px-m hover:bg-hover focus-ring"
                >
                  <span id={`${bindingId}-${id}`} className="flex">
                    {recording === id ? (
                      <span className="text-muted">Press a key…</span>
                    ) : draft[id] ? (
                      <KeyChips shortcut={draft[id]} />
                    ) : (
                      <span className="text-muted">None</span>
                    )}
                  </span>
                </button>
                <IconButton
                  icon="rotate-ccw"
                  label={`Reset ${label}`}
                  disabled={draft[id] === standard}
                  onClick={() => assign(id, standard, true)}
                  className="size-row rounded-control disabled:text-disabled disabled:hover:bg-transparent"
                />
                <IconButton
                  icon="x"
                  label={`Clear ${label}`}
                  disabled={draft[id] === null}
                  onClick={() => assign(id, null)}
                  className="size-row rounded-control disabled:text-disabled disabled:hover:bg-transparent"
                />
              </div>
              {text && (
                <div role="alert" className="flex items-center gap-m pb-xs text-muted">
                  <span className="min-w-0 flex-1">{text}</span>
                  {takeFrom && <Button onClick={() => reassign(id, takeFrom)}>Reassign</Button>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <p role="status" className="sr-only">
        {announcement}
      </p>
      {refusal && !serverNote && (
        <p role="alert" className="m-0">
          {refusal.reason}
        </p>
      )}
    </Dialog>
  );
}
