import type { ReactNode } from "react";
import type { DecoderInfo, FieldInfo, FieldKindWire } from "../generated/api";
import { Button } from "../kit/Button";
import { CheckList, type CheckItem } from "../kit/CheckList";
import { Select, TextField } from "../kit/Fields";
import { listed, type ScanView, type Settings } from "./scan";

/** What a field control reads and changes: the form's state, whichever resolver it belongs to. */
export interface FieldForm {
  decoders: readonly DecoderInfo[];
  /** The text of every field that is not a `Scan` field, and any settings the form does not show. */
  values: Settings;
  scans: Readonly<Record<string, ScanView>>;
  /** Browses this machine's folders; absent where the page is not the backend's own window. */
  pickFolder?: () => Promise<string | null>;
  /** Sets a field's text. `commit` is true when the text is final, such as a picked folder, so a scan may follow. */
  set: (key: string, value: string, commit?: boolean) => void;
  /** Whether everything the `Scan` field reads is filled in. */
  canScan: (key: string) => boolean;
  scan: (key: string) => void;
  toggle: (key: string, value: string) => void;
  toggleAll: (key: string) => void;
}

type Control = (field: FieldInfo, form: FieldForm) => ReactNode;

const text = (form: FieldForm, key: string) => {
  const value = form.values[key];
  return typeof value === "string" ? value : "";
};

/** A text box, with Browse beside it in the desktop app when `pick` can choose the path. */
function PathField({
  field,
  form,
  pick,
}: {
  field: FieldInfo;
  form: FieldForm;
  pick?: () => Promise<string | null>;
}) {
  return (
    <TextField
      label={field.label}
      value={text(form, field.key)}
      onChange={(value) => form.set(field.key, value)}
      action={
        pick && (
          <Button
            className="shrink-0"
            aria-label={`Browse ${field.label}`}
            onClick={() =>
              void pick().then((path) => {
                if (path !== null) form.set(field.key, path, true);
              })
            }
          >
            Browse
          </Button>
        )
      }
    />
  );
}

/** The control for each kind of field; a new kind is a new entry here, not a branch. */
const CONTROLS: Record<FieldKindWire["kind"], Control> = {
  Folder: (field, form) => <PathField field={field} form={form} pick={form.pickFolder} />,
  // The shell has no picker for files of a given type yet, so a file is typed.
  File: (field, form) => <PathField field={field} form={form} />,
  Text: (field, form) => (
    <TextField
      label={field.label}
      value={text(form, field.key)}
      onChange={(value) => form.set(field.key, value)}
    />
  ),
  Handler: (field, form) => (
    <Select
      label={field.label}
      value={text(form, field.key)}
      options={form.decoders.map((decoder) => [decoder.handler, decoder.display_name] as const)}
      onChange={(value) => form.set(field.key, value)}
    />
  ),
  Scan: (field, form) => {
    const view = form.scans[field.key];
    const items: CheckItem[] = view
      ? [
          ...view.choices.map((choice) => ({
            id: choice.value,
            label: choice.label,
            detail: choice.detail,
          })),
          ...view.missing.map((name) => ({ id: name, label: name, detail: "Not found" })),
        ]
      : [];
    return (
      <div className="flex flex-col gap-s">
        <div className="flex items-center justify-between gap-xl">
          {field.label}
          <Button
            icon="rotate-cw"
            disabled={!form.canScan(field.key)}
            onClick={() => form.scan(field.key)}
          >
            Scan
          </Button>
        </div>
        {view?.phase === "scanning" && (
          <p role="status" className="m-0 text-muted">
            Scanning…
          </p>
        )}
        {view?.phase === "done" &&
          (listed(view).length === 0 ? (
            <p role="status" className="m-0 text-muted">
              Nothing found
            </p>
          ) : (
            <CheckList
              label={field.label}
              items={items}
              checked={view.selected}
              onToggle={(value) => form.toggle(field.key, value)}
              onToggleAll={() => form.toggleAll(field.key)}
            />
          ))}
      </div>
    );
  },
};

/** The control that edits `field`, chosen by the kind of value it holds. */
export function FieldControl({ field, form }: { field: FieldInfo; form: FieldForm }) {
  return CONTROLS[field.kind.kind](field, form);
}
