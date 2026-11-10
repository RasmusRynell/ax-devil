import type { ReactNode } from "react";
import type { DecoderInfo, FieldInfo, FieldKindWire, ScanCountInfo } from "../generated/api";
import { Button } from "../kit/Button";
import { CheckList, type CheckItem } from "../kit/CheckList";
import { Select, TextField } from "../kit/Fields";
import { Refusal } from "../kit/Refusal";
import { countStatus, listed, type ScanView, type Settings } from "./scan";

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
  /** The dialog's own action, drawn beside the Scan button of a count-only scan, else on a row of its own. */
  action?: ReactNode;
}

type Control = (field: FieldInfo, form: FieldForm) => ReactNode;

/** The text a field shows: what was typed, else its default. */
export function shown(field: FieldInfo, values: Settings): string {
  const value = values[field.key];
  return typeof value === "string" ? value : fallback(field);
}

/** The text of the default the backend uses while the field is empty. */
const fallback = (field: FieldInfo) =>
  typeof field.default === "string" || typeof field.default === "number" ? `${field.default}` : "";

/** Why the typed text of an `Integer` field cannot be sent: the backend would take it for missing. */
export function misread(field: FieldInfo, values: Settings): string | undefined {
  const typed = shown(field, values).trim();
  return field.kind.kind === "Integer" && typed !== "" && !/^-?\d+$/.test(typed)
    ? "Whole number"
    : undefined;
}

/** Whether the field must be filled in: required, with no default for the backend to use. */
export const needsValue = (field: FieldInfo) => field.required && field.default == null;

const text = (form: FieldForm, field: FieldInfo) => shown(field, form.values);

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
      value={text(form, field)}
      placeholder={fallback(field)}
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

/**
 * The options of the decoder a `DecoderOptions` field edits, none for any other kind or while no decoder is chosen. The
 * text of an option is held in `values` under `<field key>.<option key>`.
 */
export function decoderOptions(
  field: FieldInfo,
  values: Settings,
  decoders: readonly DecoderInfo[],
): readonly FieldInfo[] {
  if (field.kind.kind !== "DecoderOptions") return [];
  const chosen = values[field.kind.handler_field];
  return decoders.find((decoder) => decoder.handler === chosen)?.options ?? [];
}

/** A `Scan` field whose found values are checked: Scan beside the label, the found values below. */
function ScanListControl({ field, form }: { field: FieldInfo; form: FieldForm }) {
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
}

/** A `Scan` field that only counts: Scan and the dialog's action in one row, what the scan found below. */
function ScanCountControl({
  field,
  count,
  form,
}: {
  field: FieldInfo;
  count: ScanCountInfo;
  form: FieldForm;
}) {
  const view = form.scans[field.key];
  const status = countStatus(view, count, form.canScan(field.key));
  return (
    <div className="flex flex-col gap-s">
      <div className="flex justify-end gap-m">
        <Button icon="rotate-cw" onClick={() => form.scan(field.key)}>
          Scan
        </Button>
        {form.action}
      </div>
      {view?.phase === "failed" ? (
        <Refusal reason={status} />
      ) : (
        status && (
          <p role="status" className="m-0 text-muted">
            {status}
          </p>
        )
      )}
    </div>
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
      value={text(form, field)}
      placeholder={fallback(field)}
      onChange={(value) => form.set(field.key, value)}
    />
  ),
  // Typed as text; the form sends the number (see `settingsOf`).
  Integer: (field, form) => (
    <TextField
      label={field.label}
      value={text(form, field)}
      placeholder={fallback(field)}
      onChange={(value) => form.set(field.key, value)}
    />
  ),
  Handler: (field, form) => (
    <Select
      label={field.label}
      value={text(form, field)}
      options={[
        ["", "Select a decoder"],
        ...form.decoders.map(
          (decoder) => [decoder.handler, `${decoder.display_name} (${decoder.handler})`] as const,
        ),
      ]}
      onChange={(value) => form.set(field.key, value)}
    />
  ),
  // The chosen decoder's own options, drawn by the controls of their kinds.
  DecoderOptions: (field, form) => (
    <div className="flex flex-col gap-l">
      {decoderOptions(field, form.values, form.decoders).map((option) => (
        <FieldControl
          key={option.key}
          field={{ ...option, key: `${field.key}.${option.key}` }}
          form={form}
        />
      ))}
    </div>
  ),
  Scan: (field, form) =>
    field.kind.kind === "Scan" && field.kind.count ? (
      <ScanCountControl field={field} count={field.kind.count} form={form} />
    ) : (
      <ScanListControl field={field} form={form} />
    ),
};

/** The control that edits `field`, chosen by the kind of value it holds. */
export function FieldControl({ field, form }: { field: FieldInfo; form: FieldForm }) {
  const reason = misread(field, form.values);
  return (
    <div className="flex flex-col gap-s">
      {CONTROLS[field.kind.kind](field, form)}
      {reason && <Refusal reason={reason} />}
    </div>
  );
}
