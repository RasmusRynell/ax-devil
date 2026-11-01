import { useState } from "react";
import type { ApiError } from "../generated/api";
import { SETTINGS } from "../generated/settings";
import { Dialog } from "../kit/Dialog";
import { useSettings } from "../settings";
import { Refusal, SettingField, type Row, type Value } from "./SettingField";

/** The rows Quick Setup asks for: the settings a new user is most likely to want to choose first. */
const ROWS: readonly Row[] = SETTINGS.filter((row) => row.quick_setup);

/**
 * The first-run choices, built from the settings table. Each pick is stored at once (so the theme applies live) and
 * marks Quick Setup done; closing marks it done too, so it is not offered again.
 */
export function QuickSetupDialog({ onClose }: { onClose: () => void }) {
  const { values, shortcuts, save } = useSettings();
  const [refusal, setRefusal] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const store = async (next: typeof values) => {
    setSaving(true);
    const refused = await save({ values: next, shortcuts });
    setSaving(false);
    setRefusal(refused);
    return refused;
  };
  // One write at a time: a second full-state write could finish first and bring back the older choice.
  const pick = (key: Row["key"], value: Value) => {
    if (!saving) void store({ ...values, [key]: value, "ui.quick_setup_done": true });
  };
  const done = async () => {
    const refused = values["ui.quick_setup_done"]
      ? null
      : await store({ ...values, "ui.quick_setup_done": true });
    if (!refused) onClose();
  };
  return (
    <Dialog title="Quick Setup" okLabel="Done" onOk={() => void done()} busy={saving}>
      <fieldset disabled={saving} className="m-0 flex min-w-0 flex-col gap-l border-0 p-0">
        {ROWS.map((row) => (
          <div key={row.key} className="flex flex-col gap-xs">
            <SettingField
              row={row}
              value={values[row.key]}
              onChange={(value) => pick(row.key, value)}
            />
            {refusal?.location === row.key && <Refusal reason={refusal.reason} />}
          </div>
        ))}
      </fieldset>
      {refusal && !ROWS.some((row) => row.key === refusal.location) && (
        <Refusal reason={refusal.reason} />
      )}
    </Dialog>
  );
}
