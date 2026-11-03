import { useId, useState } from "react";
import { useGrants } from "../access";
import { SETTINGS } from "../generated/settings";
import { Dialog } from "../kit/Dialog";
import { Icon } from "../kit/Icon";
import { SectionCaption } from "../kit/SectionCaption";
import { Tabs } from "../kit/Tabs";
import { useSettings } from "../settings";
import { literalSecret, Refusal, SettingField, type Row } from "./SettingField";
import { useSaveDialog } from "./useSaveDialog";

/** Every setting that has a page. */
const PAGED: readonly Row[] = SETTINGS.filter((row) => row.page !== null);

/**
 * The Settings dialog, built from the settings table: the Personal rows for everyone, the Server rows for whoever may
 * configure the server. Changes apply on OK; `onRestartNeeded` runs after a save that changed a setting that applies
 * only after a restart.
 */
export function SettingsDialog({
  onClose,
  onRestartNeeded,
}: {
  onClose: () => void;
  onRestartNeeded: () => void;
}) {
  const { values, shortcuts } = useSettings();
  const configures = useGrants().includes("configuration");
  const shown = PAGED.filter((row) => configures || row.scope === "personal");
  const pages = [...new Set(shown.flatMap((row) => row.page ?? []))];
  const [draft, setDraft] = useState(values);
  const [page, setPage] = useState(pages[0] ?? "");
  const { saving, refusal, submit } = useSaveDialog(onClose);
  const panel = useId();
  const ok = async () => {
    const outcome = await submit({ values: draft, shortcuts });
    if (outcome.kind === "refused") {
      const at = shown.find((row) => row.key === outcome.refusal.location);
      if (at?.page) setPage(at.page);
    }
    if (
      outcome.kind === "saved" &&
      shown.some((row) => !row.live && draft[row.key] !== values[row.key])
    )
      onRestartNeeded();
  };
  const rows = shown.filter((row) => row.page === page);
  const refusedRow = shown.some((row) => row.key === refusal?.location);
  return (
    <Dialog title="Settings" onOk={() => void ok()} onCancel={onClose} busy={saving}>
      <Tabs label="Settings pages" tabs={pages} value={page} onChange={setPage} panelId={panel} />
      <div id={panel} role="tabpanel" className="flex min-h-0 flex-col gap-l overflow-y-auto">
        {[...new Set(rows.map((row) => row.section))].map((section) => (
          <section key={section} className="flex flex-col gap-m">
            <SectionCaption heading={section} />
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
