import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { Shortcuts } from "../client/commands";
import { reasonOf } from "../client/errors";
import { COMMANDS, RESERVED, type CommandId } from "../generated/settings";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";
import { SearchField } from "../kit/Fields";
import { IconButton } from "../kit/IconButton";
import { KeyChips } from "../kit/KeyChips";
import { QUIET } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { shortcutOf } from "../kit/shortcut";
import { shortcutsPatch, useSettings } from "../client/settings";
import { useSaveDialog } from "./useSaveDialog";

/** Why a row's key was not taken, and, when another command holds it, the way to take it from that command. */
interface Note {
  id: CommandId;
  text: string;
  takeFrom?: { owner: CommandId; key: string };
}

/** The commands under each category, categories and then labels in alphabetical order. */
const SECTIONS = [...new Set(COMMANDS.map((command) => command.category))]
  .sort()
  .map((category) => ({
    category,
    commands: COMMANDS.filter((command) => command.category === category).sort((a, b) =>
      a.label.localeCompare(b.label),
    ),
  }));

const labelOf = (id: CommandId) => COMMANDS.find((command) => command.id === id)?.label ?? id;

/**
 * The Keyboard Shortcuts dialog. Changes apply on OK, through the backend, as the commands that changed. It cannot
 * be dismissed while that write is pending, so whatever is open beneath never acts on shortcuts the write is replacing.
 */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const { shortcuts } = useSettings();
  // What the dialog opened with: only the commands changed from it are sent.
  const [base] = useState(shortcuts);
  const [draft, setDraft] = useState<Shortcuts>(base);
  const [recording, setRecording] = useState<CommandId | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  // What the one polite live region says: recording started, a key set, cleared or reset.
  const [announcement, setAnnouncement] = useState("");
  const bindingId = useId();
  const { saving, refusal, submit } = useSaveDialog(onClose);
  // The release of a recorded key must not click the button it was recorded on.
  const swallowKeyUp = useRef(false);
  const [search, setSearch] = useState("");
  // The browser would focus the scrolling list itself; start on the search field instead.
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => body.current?.querySelector("input")?.focus(), []);
  const needle = search.trim().toLowerCase();
  const sections = SECTIONS.map(({ category, commands }) => ({
    category,
    commands: commands.filter((command) =>
      [command.label, command.category, draft[command.id] ?? ""].some((text) =>
        text.toLowerCase().includes(needle),
      ),
    ),
  })).filter(({ commands }) => commands.length > 0);

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
          : `${labelOf(id)} ${reset ? "reset to not set" : "cleared"}`,
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
  /** Stages every command's own key, like the per-row reset for all rows at once. */
  const resetAll = () => {
    setRecording(null);
    setNote(null);
    setDraft(Object.fromEntries(COMMANDS.map(({ id, key }) => [id, key])) as Shortcuts);
    setAnnouncement("All shortcuts reset to defaults");
  };
  const serverNote = COMMANDS.find((row) => refusal?.location === `shortcuts.${row.id}`);

  return (
    <Dialog
      title="Keyboard Shortcuts"
      onOk={() => void submit(shortcutsPatch(base, draft))}
      onCancel={() => {
        if (!saving) onClose();
      }}
      busy={saving}
      footerStart={<Button onClick={resetAll}>Reset to Defaults</Button>}
    >
      <div ref={body} className="flex min-h-0 flex-col gap-m">
        <SearchField
          placeholder="Search shortcuts"
          query={{ value: search, onChange: setSearch }}
        />
        <ul className="m-0 flex min-h-0 list-none flex-col gap-s overflow-y-auto p-xs">
          {sections.length === 0 && <li className="px-m text-muted">No shortcuts match</li>}
          {sections.map(({ category, commands }) => (
            <li key={category}>
              <SectionCaption heading={category} plain />
              <ul className="m-0 flex list-none flex-col p-0">
                {commands.map(({ id, label, key: standard }) => {
                  const shown = note?.id === id ? note : null;
                  const takeFrom = shown?.takeFrom;
                  const text =
                    shown?.text ?? (serverNote?.id === id && refusal ? reasonOf(refusal) : null);
                  return (
                    <li key={id} className="flex flex-col border-b border-line">
                      <div className="flex min-h-row items-center gap-m">
                        <span className="min-w-0 flex-1">{label}</span>
                        <button
                          type="button"
                          aria-label={`Key for ${label}`}
                          title="Click to change"
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
                          className={`${QUIET} justify-end`}
                        >
                          <span id={`${bindingId}-${id}`} className="flex">
                            {recording === id ? (
                              <span className="text-muted">Press a key…</span>
                            ) : draft[id] ? (
                              <KeyChips shortcut={draft[id]} />
                            ) : (
                              <span className="text-muted">Not set</span>
                            )}
                          </span>
                        </button>
                        <IconButton
                          icon="rotate-ccw"
                          label={`Reset ${label}`}
                          disabled={draft[id] === standard}
                          onClick={() => assign(id, standard, true)}
                          className="rounded-control"
                        />
                        <IconButton
                          icon="x"
                          label={`Clear ${label}`}
                          disabled={draft[id] === null}
                          onClick={() => assign(id, null)}
                          className="rounded-control"
                        />
                      </div>
                      {text && (
                        <div role="alert" className="flex items-center gap-m pb-xs text-muted">
                          <span className="min-w-0 flex-1">{text}</span>
                          {takeFrom && (
                            <Button onClick={() => reassign(id, takeFrom)}>Reassign</Button>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      </div>
      <p role="status" className="sr-only">
        {announcement}
      </p>
      {refusal && !serverNote && (
        <p role="alert" className="m-0">
          {reasonOf(refusal)}
        </p>
      )}
    </Dialog>
  );
}
