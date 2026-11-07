import { useEffect, useLayoutEffect, useRef } from "react";
import type { DecoderInfo, NewOverlayWire, ResolverInfo } from "../generated/api";
import { BrowseButton, Button } from "../kit/Button";
import { Select, TextField } from "../kit/Fields";
import { needsValue, shown } from "../resolvers/fields";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import type { Settings } from "../resolvers/scan";
import { Refusal } from "./SettingField";

/** One overlay of the dialog: the file typed and the handler picked for it, `""` while none is. */
export interface OverlayEntry {
  key: number;
  path: string;
  picked: string;
  /** The lane's name of an overlay already in an item, until the file changes. */
  label?: NewOverlayWire["label"];
  /** Only the initial seed of the form for the decoder the row mounted with; the form owns the options after. */
  options?: NewOverlayWire["options"];
}

/** What a row has to say when OK is pressed: its decoder's options, or `null` while they are incomplete. */
export interface OverlayRead {
  settings: Settings | null;
}

/** The name of the file at `path`, without its folders. */
export const fileName = (path: string) => path.trim().split(/[\\/]/).pop() ?? "";

/**
 * The decoders that may read a file named `path`: those with no declared extension, and those whose lowercase
 * extension ends the file name. An extension with several dots (`.acme.dat`) may be the whole name; one with a single
 * dot needs a name before it, as the backend's `matches_extension` has it. A file nothing reads has none, and the
 * user picks any decoder by hand.
 */
export function decodersFor(
  decoders: readonly DecoderInfo[],
  path: string,
): readonly DecoderInfo[] {
  const name = fileName(path).toLowerCase();
  const reads = (extension: string) =>
    name.endsWith(extension) && (extension.split(".").length > 2 || name.length > extension.length);
  return decoders.filter(
    (decoder) => decoder.extensions.length === 0 || decoder.extensions.some(reads),
  );
}

/**
 * The entry after its path became `path`: the sole decoder that may read the file is chosen, a pick that may not read
 * it is dropped, and a pick that may is kept.
 */
export function withPath(
  entry: OverlayEntry,
  path: string,
  decoders: readonly DecoderInfo[],
): OverlayEntry {
  const candidates = path.trim() ? decodersFor(decoders, path) : [];
  const picked =
    candidates.length === 1
      ? candidates[0].handler
      : candidates.some((one) => one.handler === entry.picked)
        ? entry.picked
        : "";
  return { ...entry, path, picked, label: null };
}

/** Whether the entry names a file but no handler for it yet. */
export const undecided = (entry: OverlayEntry) => !!entry.path.trim() && !entry.picked;

/** A decoder's options as the form a resolver's settings are, so the same field controls draw them. */
const optionsForm = (decoder?: DecoderInfo): ResolverInfo => ({
  id: decoder?.handler ?? "",
  display_name: decoder?.display_name ?? "",
  description: "",
  fields: decoder?.options ?? [],
});

/**
 * An overlay file with the handler that reads it and the options that handler declares. `read` is how the dialog
 * asks for the options when OK is pressed; `tried` shows what a required option still lacks.
 */
export function OverlayRow({
  number,
  entry,
  decoders,
  focus,
  problem,
  tried,
  onBrowse,
  onChange,
  onRemove,
  register,
}: {
  /** The row's place in the dialog, counting from 1, which tells its controls from the other rows'. */
  number: number;
  entry: OverlayEntry;
  decoders: readonly DecoderInfo[];
  /** Puts the cursor in the file field when the row appears. */
  focus: boolean;
  /** Why the backend cannot read the file, once it has said. */
  problem?: string;
  tried: boolean;
  /** Opens the file chooser for the overlay; absent where the page may not browse the machine's files. */
  onBrowse?: () => void;
  onChange: (entry: OverlayEntry) => void;
  onRemove: () => void;
  register: (key: number, read: (() => OverlayRead) | null) => void;
}) {
  const decoder = decoders.find((one) => one.handler === entry.picked);
  const form = useResolverForm(optionsForm(decoder), decoders, entry.options);
  const named = entry.path.trim() !== "";
  const missing = form.resolver.fields.find(
    (field) => needsValue(field) && !shown(field, form.values).trim(),
  );
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) row.current?.querySelector("input")?.focus();
  }, [focus]);
  useLayoutEffect(() => {
    register(entry.key, () => ({ settings: form.settings }));
    return () => register(entry.key, null);
  });
  return (
    <div ref={row} role="group" aria-label={`Overlay ${number}`} className="flex flex-col gap-s">
      <TextField
        label="Overlay path"
        value={entry.path}
        onChange={(path) => onChange(withPath(entry, path, decoders))}
        action={
          <>
            {onBrowse && <BrowseButton what="overlay file" onBrowse={onBrowse} />}
            <Button
              size="small"
              icon="trash-2"
              label={`Remove overlay ${number}`}
              onClick={onRemove}
            />
          </>
        }
      />
      <Select
        label="Data handler"
        value={entry.picked}
        disabled={!named}
        options={[
          ["", "(None)"],
          ...decoders.map((one) => [one.handler, one.display_name] as const),
        ]}
        onChange={(picked) => onChange({ ...entry, picked })}
      />
      {problem ? (
        <Refusal reason={problem} />
      ) : (
        undecided(entry) && (
          <Refusal reason={`Choose the data handler that reads ${fileName(entry.path)}.`} />
        )
      )}
      {named && decoder && <ResolverForm form={form} />}
      {tried && named && missing && <Refusal reason={`${missing.label} is required`} />}
    </div>
  );
}
