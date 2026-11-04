import { useEffect, useLayoutEffect, useRef } from "react";
import type { DecoderInfo, ResolverInfo } from "../generated/api";
import { Button } from "../kit/Button";
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
}

/** What a row has to say when OK is pressed: its decoder's options, or `null` while they are incomplete. */
export interface OverlayRead {
  settings: Settings | null;
}

/** The name of the file at `path`, without its folders. */
export const fileName = (path: string) => path.trim().split(/[\\/]/).pop() ?? "";

/**
 * The decoders that may read a file named `path`: those with no declared extension, and those whose lowercase
 * extension ends the file name, which may hold several dots (`.acme.dat`). A file nothing reads has none, and the
 * user picks any decoder by hand.
 */
export function decodersFor(
  decoders: readonly DecoderInfo[],
  path: string,
): readonly DecoderInfo[] {
  const name = fileName(path).toLowerCase();
  return decoders.filter(
    (decoder) =>
      decoder.extensions.length === 0 ||
      decoder.extensions.some(
        (extension) => name.length > extension.length && name.endsWith(extension),
      ),
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
  return { ...entry, path, picked };
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
  entry,
  decoders,
  focus,
  tried,
  onChange,
  onRemove,
  register,
}: {
  entry: OverlayEntry;
  decoders: readonly DecoderInfo[];
  /** Puts the cursor in the file field when the row appears. */
  focus: boolean;
  tried: boolean;
  onChange: (entry: OverlayEntry) => void;
  onRemove: () => void;
  register: (key: number, read: (() => OverlayRead) | null) => void;
}) {
  const decoder = decoders.find((one) => one.handler === entry.picked);
  const form = useResolverForm(optionsForm(decoder), decoders);
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
    <div ref={row} className="flex flex-col gap-s">
      <TextField
        label="Overlay path"
        value={entry.path}
        onChange={(path) => onChange(withPath(entry, path, decoders))}
        action={<Button size="small" icon="trash-2" label="Remove overlay" onClick={onRemove} />}
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
      {undecided(entry) && (
        <Refusal reason={`Choose the data handler that reads ${fileName(entry.path)}.`} />
      )}
      {named && decoder && <ResolverForm form={form} />}
      {tried && named && missing && <Refusal reason={`${missing.label} is required`} />}
    </div>
  );
}
