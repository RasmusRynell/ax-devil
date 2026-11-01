import { useId, useState } from "react";
import { SETTINGS } from "../generated/settings";
import { Dialog } from "../kit/Dialog";
import { Icon } from "../kit/Icon";
import { useSettings } from "../settings";
import { literalSecret, Refusal, SettingField, type Row } from "./SettingField";
import { useSaveDialog } from "./useSaveDialog";

/** The rows the dialog shows: every setting that has a page. */
const ROWS: readonly Row[] = SETTINGS.filter((row) => row.page !== null);
const PAGES = [...new Set(ROWS.map((row) => row.page))];

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
                const warning = literalSecret(row, draft[row.key]);
                const note = `${panel}-${row.key}`;
                return (
                  <div key={row.key} className="flex flex-col gap-xs">
                    <SettingField
                      row={row}
                      value={draft[row.key]}
                      describedBy={warning ? note : undefined}
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
                    {warning && (
                      <p id={note} className="m-0 flex items-center gap-s text-muted">
                        <Icon name="triangle-alert" />
                        {row.env
                          ? "Saved as plain text. Use $VARIABLE to read it from the environment."
                          : "Saved as plain text."}
                      </p>
                    )}
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
