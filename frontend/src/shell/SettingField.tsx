import type { ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { BrowseButton } from "../kit/Button";
import { SegmentedControl } from "../kit/Chips";
import { Checkbox, LABELLED, Select, TextField } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { TONE } from "../kit/look";
import { LoginsEditor } from "./LoginsEditor";

/** A setting's value as the backend stores it. */
export type Value = SettingsState["values"][SettingKey];
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
  /** Whom the setting is for: `personal` is one person's own, `server` is for whoever may configure the server. */
  readonly scope: "personal" | "server";
  readonly page: string | null;
  readonly section: string;
  readonly live: boolean;
  readonly env: boolean;
  readonly secret: boolean;
  readonly quick_setup: boolean;
}

/** The value as text; the sticky and sync settings and lists of records are not text, and no field shows them. */
export const textOf = (value: Value) => (typeof value === "object" ? "" : String(value));

/** Whether the whole value is a `$NAME` reference to an environment variable. */
export const isReference = (value: string) => /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value);

/** Whether a secret row holds the secret itself, which is then masked and warned about, not a reference to it. */
export function literalSecret(row: Row, value: Value) {
  const text = textOf(value);
  return row.secret && text !== "" && !(row.env && isReference(text));
}

interface FieldProps {
  row: Row;
  label: ReactNode;
  value: Value;
  describedBy?: string;
  /** For a decoder: the installed live decoders to offer, as `[id, name]`. */
  decoders?: readonly (readonly [string, string])[];
  onChange: (value: Value) => void;
}

/** The control for each kind of setting; a new kind is a new entry here, not a branch. */
const FIELDS: Record<KindTag, (props: FieldProps) => ReactNode> = {
  bool: ({ label, value, onChange }) => (
    <Checkbox label={label} checked={value === true} onChange={onChange} />
  ),
  // Set from a lane's menu, so no settings page lists them.
  sticky: () => null,
  sync: () => null,
  object_filter: () => null,
  choice: ({ row, label, value, onChange }) =>
    row.kind.toggle ? (
      <div className={LABELLED}>
        {label}
        <SegmentedControl
          label={row.label}
          value={textOf(value)}
          options={row.kind.options ?? []}
          onChange={onChange}
        />
      </div>
    ) : row.env ? (
      // Any text stays allowed so the mode can be a `$NAME` reference; the options are offered to pick from.
      <TextField
        label={label}
        value={textOf(value)}
        suggestions={row.kind.options ?? []}
        onChange={onChange}
      />
    ) : (
      <Select
        label={label}
        value={textOf(value)}
        options={row.kind.options ?? []}
        onChange={onChange}
      />
    ),
  auto_or_whole: ({ label, value, onChange }) => (
    <TextField
      label={label}
      value={textOf(value)}
      onChange={(text) => onChange(/^\d+$/.test(text) ? Number(text) : text)}
    />
  ),
  whole: ({ label, value, onChange }) => (
    <TextField
      label={label}
      value={textOf(value)}
      onChange={(text) => onChange(/^\d+$/.test(text) ? Number(text) : text)}
    />
  ),
  // The scan logins are the only list of records on a page (the confirmed references have none): edited in place,
  // saved with the rest on OK.
  records: ({ value, onChange }) => (
    <LoginsEditor logins={Array.isArray(value) ? value : []} onChange={onChange} />
  ),
  text: ({ row, label, value, describedBy, onChange }) => (
    <TextField
      label={label}
      value={textOf(value)}
      masked={literalSecret(row, value)}
      describedBy={describedBy}
      wide={row.env}
      onChange={onChange}
    />
  ),
  // The id is what is saved; any text stays allowed, so a decoder that is not installed here can still be the default.
  decoder: ({ label, value, decoders, onChange }) => (
    <TextField label={label} value={textOf(value)} suggestions={decoders} onChange={onChange} />
  ),
  folder: ({ row, label, value, onChange }) => {
    const bridge = window.axDevilWindow;
    return (
      <TextField
        label={label}
        value={textOf(value)}
        onChange={onChange}
        action={
          bridge && (
            <BrowseButton
              what={row.label}
              onBrowse={() =>
                void bridge.pickFolder(row.label).then((folder) => {
                  if (folder !== null) onChange(folder);
                })
              }
            />
          )
        }
      />
    );
  },
};

/** The control for a setting row, chosen by its kind; `label` defaults to the row's own. */
export function SettingField({
  row,
  label = row.label,
  ...props
}: Omit<FieldProps, "label"> & { label?: ReactNode }) {
  const Field = FIELDS[row.kind.type];
  return <Field row={row} label={label} {...props} />;
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

/** Why the backend refused a value, shown at the value in the danger tone; `title` is the fuller reason on hover. */
export function Refusal({ reason, title }: { reason: string; title?: string }) {
  return (
    <p role="alert" title={title} className={`m-0 flex items-center gap-s ${TONE.danger.text}`}>
      <Icon name="triangle-alert" />
      <span className="min-w-0 [overflow-wrap:anywhere]">{reason}</span>
    </p>
  );
}
