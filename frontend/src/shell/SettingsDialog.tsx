import { useState } from "react";
import { Dialog } from "../kit/Dialog";
import { Select } from "../kit/Fields";
import { THEMES, useSettings } from "../settings";

/** The Settings dialog. Changes apply on OK, through the backend; a refusal stays in the dialog. */
export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const { theme, save } = useSettings();
  const [draft, setDraft] = useState(theme);
  const [failure, setFailure] = useState<string | null>(null);
  const ok = async () => {
    const reason = await save(draft);
    if (reason === null) onClose();
    else setFailure(reason);
  };
  return (
    <Dialog title="Settings" onOk={() => void ok()} onCancel={onClose}>
      <div role="tablist" className="flex border-b border-line">
        <button
          type="button"
          role="tab"
          aria-selected="true"
          className="-mb-px border-b-2 border-accent bg-transparent px-m py-s text-fg focus-visible:outline-2 focus-visible:outline-accent"
        >
          General
        </button>
      </div>
      <div role="tabpanel" className="flex flex-col gap-m">
        <Select label="Theme" value={draft} options={THEMES} onChange={setDraft} />
      </div>
      {failure && (
        <p role="alert" className="m-0">
          {failure}
        </p>
      )}
    </Dialog>
  );
}
