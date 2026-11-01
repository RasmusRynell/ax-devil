import { useId, useState, type ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import { SETTINGS, type SettingKey } from "../generated/settings";
import { Dialog } from "../kit/Dialog";
import { Checkbox, Select, TextField } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { useSettings } from "../settings";
import { useSaveDialog } from "./useSaveDialog";

type Value = SettingsState["values"][SettingKey];
type KindTag = "bool" | "choice" | "auto_or_whole" | "text";

/** A table row as the dialog reads it. */
interface Row {
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
}

const ROWS: readonly Row[] = SETTINGS.filter((row) => row.page !== null);
const PAGES = [...new Set(ROWS.map((row) => row.page))];

/** Whether the whole value is a `$NAME` reference to an environment variable. */
const isReference = (value: string) => /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value);

interface FieldProps {
  row: Row;
  label: ReactNode;
  value: Value;
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
  text: ({ row, label, value, onChange }) => (
    <TextField
      label={label}
      value={String(value)}
      masked={row.secret && !(row.env && isReference(String(value)))}
      onChange={onChange}
    />
  ),
};

/** The Settings dialog, built from the settings table. Changes apply on OK, through the backend. */
export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const { values, shortcuts } = useSettings();
  const [draft, setDraft] = useState(values);
  const [page, setPage] = useState(PAGES[0]);
  const { saving, refusal, submit } = useSaveDialog(onClose);
  const panel = useId();
  const ok = async () => {
    const refused = await submit({ values: draft, shortcuts });
    const at = ROWS.find((row) => row.key === refused?.location);
    if (at) setPage(at.page);
  };
  const rows = ROWS.filter((row) => row.page === page);
  const refusedRow = ROWS.some((row) => row.key === refusal?.location);
  return (
    <Dialog title="Settings" onOk={() => void ok()} onCancel={onClose} busy={saving}>
      <div role="tablist" className="flex border-b border-line">
        {PAGES.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === page}
            aria-controls={panel}
            onClick={() => setPage(name)}
            className={`-mb-hairline border-b-edge bg-transparent px-m py-s text-fg focus-ring ${name === page ? "border-accent" : "border-transparent"}`}
          >
            {name}
          </button>
        ))}
      </div>
      <div id={panel} role="tabpanel" className="flex flex-col gap-l">
        {[...new Set(rows.map((row) => row.section))].map((section) => (
          <section key={section} className="flex flex-col gap-m">
            <h3 className="m-0 text-small font-semibold text-muted uppercase">{section}</h3>
            {rows
              .filter((row) => row.section === section)
              .map((row) => {
                const Field = FIELDS[row.kind.type];
                return (
                  <div key={row.key} className="flex flex-col gap-xs">
                    <Field
                      row={row}
                      value={draft[row.key]}
                      onChange={(value) => setDraft({ ...draft, [row.key]: value })}
                      label={
                        <span className="flex items-center gap-s">
                          {row.label}
                          {!row.live && (
                            <span title="Applies after a restart" className="text-muted">
                              <Icon name="rotate-cw" />
                            </span>
                          )}
                        </span>
                      }
                    />
                    {refusal?.location === row.key && <Refusal reason={refusal.reason} />}
                  </div>
                );
              })}
          </section>
        ))}
      </div>
      {refusal && !refusedRow && <Refusal reason={refusal.reason} />}
    </Dialog>
  );
}

/** Why the backend refused a value, shown at the value. */
function Refusal({ reason }: { reason: string }) {
  return (
    <p role="alert" className="m-0 flex items-center gap-s">
      <Icon name="triangle-alert" />
      {reason}
    </p>
  );
}
