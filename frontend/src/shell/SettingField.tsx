import type { ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { Checkbox, Select, TextField } from "../kit/Fields";
import { Icon } from "../kit/Icon";

/** A setting's value as the backend stores it. */
export type Value = SettingsState["values"][SettingKey];
type KindTag = "bool" | "choice" | "auto_or_whole" | "text" | "folder";

/** A table row as the fields read it. */
export interface Row {
  readonly key: SettingKey;
  readonly label: string;
  readonly kind: {
    readonly type: KindTag;
    readonly options?: readonly (readonly [string, string])[];
  };
  readonly page: string | null;
  readonly section: string;
  readonly live: boolean;
  readonly env: boolean;
  readonly secret: boolean;
  readonly quick_setup: boolean;
}

/** Whether the whole value is a `$NAME` reference to an environment variable. */
const isReference = (value: string) => /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value);

/** Whether a secret row holds the secret itself, which is then masked and warned about, not a reference to it. */
export function literalSecret(row: Row, value: Value) {
  const text = String(value);
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
  choice: ({ row, label, value, onChange }) => (
    <Select
      label={label}
      value={String(value)}
      options={row.kind.options ?? []}
      onChange={onChange}
    />
  ),
  auto_or_whole: ({ label, value, onChange }) => (
    <TextField
      label={label}
      value={String(value)}
      onChange={(text) => onChange(/^\d+$/.test(text) ? Number(text) : text)}
    />
  ),
  text: ({ row, label, value, describedBy, onChange }) => (
    <TextField
      label={label}
      value={String(value)}
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
        value={String(value)}
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

/** Why the backend refused a value, shown at the value. */
export function Refusal({ reason }: { reason: string }) {
  return (
    <p role="alert" className="m-0 flex items-center gap-s">
      <Icon name="triangle-alert" />
      {reason}
    </p>
  );
}
