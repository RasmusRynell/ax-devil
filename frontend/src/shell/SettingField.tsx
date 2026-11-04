import type { ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { Checkbox, Select, TextField } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { TONE } from "../kit/look";

/** A setting's value as the backend stores it. */
export type Value = SettingsState["values"][SettingKey];
type KindTag =
  | "bool"
  | "sticky"
  | "sync"
  | "choice"
  | "auto_or_whole"
  | "whole"
  | "text"
  | "folder"
  | "records";

/** A table row as the fields read it. */
export interface Row {
  readonly key: SettingKey;
  readonly label: string;
  readonly kind: {
    readonly type: KindTag;
    readonly options?: readonly (readonly [string, string])[];
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
const isReference = (value: string) => /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value);

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
  choice: ({ row, label, value, onChange }) => (
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
  // Lists of records (confirmed references, scan logins) have no field yet; no row with one has a page.
  records: () => null,
  text: ({ row, label, value, describedBy, onChange }) => (
    <TextField
      label={label}
      value={textOf(value)}
      masked={literalSecret(row, value)}
      describedBy={describedBy}
      onChange={onChange}
    />
  ),
  folder: ({ label, value, onChange }) => {
    const bridge = window.axDevilWindow;
    return (
      <TextField
        label={label}
        value={textOf(value)}
        onChange={onChange}
        action={
          bridge && (
            <Button
              className="shrink-0"
              onClick={() =>
                void bridge.pickFolder().then((folder) => {
                  if (folder !== null) onChange(folder);
                })
              }
            >
              Browse
            </Button>
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
