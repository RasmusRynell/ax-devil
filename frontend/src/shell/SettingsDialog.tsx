import { useId, useState } from "react";
import { useAccess } from "../client/access";
import { reasonOf } from "../client/errors";
import { SETTINGS } from "../generated/settings";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";
import { LABELLED } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { SectionCaption } from "../kit/SectionCaption";
import { Tabs } from "../kit/Tabs";
import { useHandlers } from "../resolvers/useHandlers";
import { settingsPatch, useSettings } from "../client/settings";
import { literalSecret, PlainTextNote, SettingField, type Row } from "./SettingField";
import { Refusal } from "../kit/Refusal";
import { ShortcutsDialog } from "./ShortcutsDialog";
import type { ManualCache } from "./VideoCacheField";
import { numericInput } from "./numericInput";
import { useRepairIntent } from "./useRepairIntent";
import { useSaveDialog } from "./useSaveDialog";

/** Every setting that has a page. */
const PAGED: readonly Row[] = SETTINGS.filter((row) => row.page !== null);

/**
 * The Settings dialog, built from the settings table: the Device rows for everyone, the Personal and Server rows for
 * the Owner. Changes apply on OK; `onRestartNeeded` runs after a save that changed a setting that applies
 * only after a restart. Keyboard shortcuts open in a dialog of their own over this one, which stores them when it is
 * confirmed, even if this one is cancelled.
 */
export function SettingsDialog({
  onClose,
  onRestartNeeded,
}: {
  onClose: () => void;
  onRestartNeeded: () => void;
}) {
  const { values, savedValues, file, recovered } = useSettings();
  // A page that holds only a join link has only the Device settings.
  const { owner, fullAccess } = useAccess();
  const shown = PAGED.filter((row) => owner || row.scope === "device");
  const pages = [
    ...new Set([...shown.flatMap((row) => row.page ?? []), ...(file ? ["Advanced"] : [])]),
  ];
  // What the dialog opened with: only the settings changed from it are sent, so another window's change to the rest stays.
  const [base] = useState(savedValues);
  const [opened] = useState(values);
  const [draft, setDraft] = useState(opened);
  const [manualCache, setManualCache] = useState<ManualCache>(() => {
    const value = opened["playback.video_cache"];
    const mib = typeof value === "number" ? value : 1024;
    return { text: (mib / 1024).toFixed(2), mib };
  });
  const repair = useRepairIntent(recovered);
  const [page, setPage] = useState(pages[0] ?? "");
  const [editingShortcuts, setEditingShortcuts] = useState(false);
  const { saving, refusal, submit } = useSaveDialog(onClose);
  const panel = useId();
  // Only a decoder of streaming, timestamped records can decode a live stream.
  const decoders = (useHandlers().handlers?.decoders ?? [])
    .filter((decoder) => decoder.live)
    .map((decoder) => [decoder.handler, decoder.display_name] as const);
  const ok = async () => {
    const normalized = { ...draft };
    for (const row of shown) {
      const value = draft[row.key];
      if (
        value === base[row.key] ||
        typeof value !== "string" ||
        row.secret ||
        (row.full_access && !fullAccess)
      )
        continue;
      if (row.kind.type === "whole" || row.kind.type === "auto_or_whole")
        normalized[row.key] = numericInput(value, row.kind.type);
    }
    const outcome = await submit(settingsPatch(base, normalized, opened, repair.keys()));
    if (outcome.kind === "refused") {
      const at = shown.find((row) => row.key === outcome.refusal.location);
      if (at?.page) setPage(at.page);
    }
    if (
      outcome.kind === "saved" &&
      shown.some((row) => !row.live && draft[row.key] !== base[row.key])
    )
      onRestartNeeded();
  };
  const refusedRow = shown.some((row) => row.key === refusal?.location);
  return (
    <>
      <Dialog
        title={<span className="sr-only [@media(min-height:20rem)]:not-sr-only">Settings</span>}
        onOk={() => void ok()}
        onCancel={onClose}
        busy={saving}
      >
        <Tabs
          label="Settings pages"
          tabs={pages}
          value={page}
          onChange={setPage}
          panelId={panel}
          scrollable
        />
        {/* Every page sits in one grid cell, so the dialog is as tall as its tallest page on every tab. */}
        <div className="grid min-h-0 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)]">
          {pages.map((name) => {
            const rows = shown.filter((row) => row.page === name);
            const current = name === page;
            return (
              <div
                key={name}
                id={current ? panel : undefined}
                role={current ? "tabpanel" : undefined}
                aria-label={current ? name : undefined}
                aria-hidden={current ? undefined : true}
                inert={!current}
                // Positioned, so the hidden radios of a toggle further down scroll with it instead of stretching the dialog.
                className={`relative col-start-1 row-start-1 flex min-h-0 flex-col gap-l overflow-y-auto ${current ? "" : "invisible"}`}
              >
                {[...new Set(rows.map((row) => row.section))].map((section) => (
                  <section key={section} className="flex flex-col gap-m">
                    {section && <SectionCaption heading={section} plain />}
                    {rows
                      .filter((row) => row.section === section)
                      .map((row) => {
                        const warning = literalSecret(row, draft[row.key]);
                        const note = `${panel}-${row.key}`;
                        return (
                          <div
                            key={row.key}
                            title={row.description ?? row.key}
                            className="flex flex-col gap-xs"
                          >
                            <fieldset
                              disabled={row.full_access && !fullAccess}
                              className="contents"
                            >
                              <SettingField
                                row={row}
                                cacheDraft={
                                  row.key === "playback.video_cache"
                                    ? { manual: manualCache, onManualChange: setManualCache }
                                    : undefined
                                }
                                value={draft[row.key]}
                                describedBy={warning ? note : undefined}
                                decoders={decoders}
                                onChange={(value) => {
                                  repair.edit(row.key);
                                  setDraft({ ...draft, [row.key]: value });
                                }}
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
                            </fieldset>
                            {warning && <PlainTextNote id={note} reference={row.env} />}
                            {refusal?.location === row.key && (
                              <Refusal reason={reasonOf(refusal)} />
                            )}
                          </div>
                        );
                      })}
                  </section>
                ))}
                {name === "Advanced" && file && (
                  <>
                    {(
                      [
                        ["Configuration file", file.path],
                        ["Configuration version", String(file.version)],
                      ] as const
                    ).map(([name, shownValue]) => (
                      <div key={name} className={LABELLED}>
                        {name}
                        <span className="min-w-0 text-muted [overflow-wrap:anywhere] select-text">
                          {shownValue}
                        </span>
                      </div>
                    ))}
                  </>
                )}
                {name === "General" && owner && (
                  <Button
                    className="self-start"
                    disabled={saving}
                    onClick={() => setEditingShortcuts(true)}
                  >
                    Keyboard shortcuts
                  </Button>
                )}
              </div>
            );
          })}
        </div>
        {refusal && !refusedRow && <Refusal reason={reasonOf(refusal)} />}
      </Dialog>
      {/* A sibling, not a child: a dialog's form cannot hold another form. */}
      {editingShortcuts && <ShortcutsDialog onClose={() => setEditingShortcuts(false)} />}
    </>
  );
}
