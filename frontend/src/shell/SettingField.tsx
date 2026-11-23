import { VideoCacheField, type ManualCache } from "./VideoCacheField";
import type { ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { SegmentedControl } from "../kit/Chips";
import { FormCheck, FormInput, FormPath, FormRow, FormSelect } from "../kit/Form";
import { Icon } from "../kit/Icon";
import { SET_PASSWORD } from "../live/expansions";
import { chooseFolder, useBrowse } from "../client/pickPath";
import { isSecretState, secretStateOf, type Login } from "../client/secret";
import { numericInput } from "./numericInput";
import { LoginsEditor } from "./LoginsEditor";

/** A setting's value as the backend tells it, or as edited: logins carry the passwords typed since. */
export type Value = SettingsState["values"][SettingKey] | Login[];
type KindTag =
  | "bool"
  | "sticky"
  | "sync"
  | "object_filter"
  | "choice"
  | "auto_or_whole"
  | "whole"
  | "text"
  | "decoder"
  | "folder"
  | "records";

/** A table row as the fields read it. */
export interface Row {
  readonly key: SettingKey;
  readonly label: string;
  /** What the setting does, or `null`. */
  readonly description: string | null;
  readonly kind: {
    readonly type: KindTag;
    readonly options?: readonly (readonly [string, string])[];
    /** For a choice: whether it is a segmented control. */
    readonly toggle?: boolean;
  };
  /** Where the setting lives: `device` on the screen in front of you, `personal` one Owner's own, `server` the server's. */
  readonly scope: "device" | "personal" | "server";
  readonly page: string | null;
  readonly section: string;
  readonly live: boolean;
  readonly env: boolean;
  readonly secret: boolean;
  /** Whether only an Owner with Full access may change it. */
  readonly full_access: boolean;
  readonly quick_setup: boolean;
}

/**
 * The value as text: a secret that is not being edited as the reference it holds (a literal is not known here, so
 * it is empty); the sticky and sync settings and lists of records are not text, and no field shows them.
 */
export const textOf = (value: Value) =>
  isSecretState(value)
    ? value.state === "reference"
      ? `$${value.name}`
      : ""
    : typeof value === "object"
      ? ""
      : String(value);

/** Whether a secret row holds the secret itself, which is then masked and warned about, not a reference to it. */
export function literalSecret(row: Row, value: Value) {
  if (!row.secret) return false;
  if (isSecretState(value)) return value.state === "literal";
  return secretStateOf(textOf(value), row.env).state === "literal";
}

interface FieldProps {
  cacheDraft?: { manual: ManualCache; onManualChange: (value: ManualCache) => void };
  row: Row;
  value: Value;
  /** What sits after the row's label, such as the restart mark. */
  marker?: ReactNode;
  /** Lines under the row's control. */
  note?: ReactNode;
  describedBy?: string;
  /** For a decoder: the installed live decoders to offer, as `[id, name]`. */
  decoders?: readonly (readonly [string, string])[];
  onChange: (value: Value) => void;
}

/** The folder row: its button where this window can choose a folder. */
function FolderField({ row, value, marker, note, onChange }: FieldProps) {
  const browse = useBrowse(chooseFolder(row.label), onChange);
  return (
    <FormPath
      label={row.label}
      marker={marker}
      note={note}
      what={row.label}
      value={textOf(value)}
      browse={browse}
      onChange={onChange}
    />
  );
}

/** The row for each kind of setting: a label and its control; a new kind is a new entry here, not a branch. */
const FIELDS: Record<KindTag, (props: FieldProps) => ReactNode> = {
  bool: ({ row, value, marker, note, onChange }) => (
    <FormCheck
      label={row.label}
      marker={marker}
      note={note}
      checked={value === true}
      onChange={onChange}
    />
  ),
  // Set from a lane's menu, so no settings page lists them.
  sticky: () => null,
  sync: () => null,
  object_filter: () => null,
  choice: ({ row, value, marker, note, onChange }) =>
    row.kind.toggle ? (
      <FormRow label={row.label} marker={marker} note={note}>
        <SegmentedControl
          label={row.label}
          value={textOf(value)}
          options={row.kind.options ?? []}
          onChange={onChange}
        />
      </FormRow>
    ) : row.env ? (
      // Any text stays allowed so the mode can be a `$NAME` reference; the options are offered to pick from.
      <FormInput
        label={row.label}
        marker={marker}
        note={note}
        value={textOf(value)}
        suggestions={row.kind.options ?? []}
        onChange={onChange}
      />
    ) : (
      <FormSelect
        label={row.label}
        marker={marker}
        note={note}
        value={textOf(value)}
        options={row.kind.options ?? []}
        onChange={onChange}
      />
    ),
  auto_or_whole: ({ row, value, marker, note, onChange, cacheDraft }) =>
    cacheDraft ? (
      <VideoCacheField
        {...cacheDraft}
        label={row.label}
        marker={marker}
        note={note}
        value={typeof value === "number" ? value : textOf(value)}
        onChange={onChange}
      />
    ) : (
      <FormInput
        label={row.label}
        marker={marker}
        note={note}
        value={textOf(value)}
        onChange={(text) => onChange(numericInput(text, "auto_or_whole", true))}
      />
    ),
  whole: ({ row, value, marker, note, onChange }) => (
    <FormInput
      label={row.label}
      marker={marker}
      note={note}
      value={textOf(value)}
      onChange={(text) => onChange(numericInput(text, "whole", true))}
    />
  ),
  // The scan logins are the only list of records on a page (the confirmed references have none): edited in place,
  // saved with the rest on OK.
  records: ({ value, note, onChange }) => (
    <div className="col-span-full flex min-w-0 flex-col gap-xs">
      <LoginsEditor headed logins={Array.isArray(value) ? value : []} onChange={onChange} />
      {note}
    </div>
  ),
  text: ({ row, value, marker, note, describedBy, onChange }) => (
    <FormInput
      label={row.label}
      marker={marker}
      note={note}
      value={textOf(value)}
      masked={literalSecret(row, value)}
      // A saved secret is not known here: the dots only say it is set, and typing replaces it.
      placeholder={isSecretState(value) && value.state === "literal" ? SET_PASSWORD : undefined}
      describedBy={describedBy}
      onChange={onChange}
    />
  ),
  // The id is what is saved; any text stays allowed, so a decoder that is not installed here can still be the default.
  decoder: ({ row, value, marker, note, decoders, onChange }) => (
    <FormInput
      label={row.label}
      marker={marker}
      note={note}
      value={textOf(value)}
      suggestions={decoders}
      onChange={onChange}
    />
  ),
  folder: (props) => <FolderField {...props} />,
};

/** The row for a setting, chosen by its kind. */
export function SettingField({ row, ...props }: FieldProps) {
  const Field = FIELDS[row.kind.type];
  return <Field row={row} {...props} />;
}

/** The warning under a secret field that holds the secret itself; `id` is what the field's `aria-describedby` names. */
export function PlainTextNote({ id, reference }: { id: string; reference: boolean }) {
  return (
    <p id={id} className="m-0 flex items-center gap-s text-muted">
      <Icon name="triangle-alert" />
      {reference
        ? "Saved as plain text. Use $VARIABLE to read it from the environment."
        : "Saved as plain text."}
    </p>
  );
}
