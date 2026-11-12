import { reasonOf } from "../client/errors";
import { useEffect, useState, type ReactNode } from "react";
import { SETTINGS, type SettingKey } from "../generated/settings";
import { SegmentedControl, TileChoice } from "../kit/Chips";
import { Dialog, DialogBody } from "../kit/Dialog";
import { SectionCaption } from "../kit/SectionCaption";
import { isEmpty, settingsPatch, useAppearance, useSettings } from "../client/settings";
import { textOf, type Row } from "./SettingField";
import { useSaveDialog } from "./useSaveDialog";

/** The rows Quick Setup asks for: the settings a new user is most likely to want to choose first. */
const ROWS: readonly Row[] = SETTINGS.filter((row) => row.quick_setup);

/** The classes of a miniature window's parts in each theme, whatever theme the app itself shows. */
const WINDOW = {
  light: {
    box: "border-line-strong-light bg-surface-light",
    panel: "bg-panel-light",
    line: "bg-muted-light",
    accent: "bg-accent-light",
  },
  dark: {
    box: "border-line-strong-dark bg-surface-dark",
    panel: "bg-panel-dark",
    line: "bg-muted-dark",
    accent: "bg-accent-dark",
  },
};

/** A miniature app window in one theme's colours. */
function MiniWindow({ theme, className = "" }: { theme: keyof typeof WINDOW; className?: string }) {
  const look = WINDOW[theme];
  return (
    <span
      className={`${look.box} ${className} absolute inset-0 flex flex-col overflow-hidden rounded-control border`}
    >
      <span className={`h-1/5 ${look.panel}`} />
      <span className="flex min-h-0 flex-1">
        <span className={`w-1/4 ${look.panel}`} />
        <span className="flex flex-1 flex-col gap-s p-s">
          <span className={`h-xs w-4/5 rounded-pill ${look.line}`} />
          <span className={`h-xs w-3/5 rounded-pill ${look.line}`} />
          <span className={`h-xs w-2/3 rounded-pill ${look.line}`} />
          <span className={`h-s w-1/4 rounded-pill ${look.accent}`} />
        </span>
      </span>
    </span>
  );
}

/** The System theme shows both windows, the dark one below the diagonal. */
function ThemePreview({ theme }: { theme: string }) {
  return (
    <span aria-hidden="true" className="relative block aspect-8/5 w-preview">
      {theme !== "dark" && <MiniWindow theme="light" />}
      {theme !== "light" && (
        <MiniWindow theme="dark" className={theme === "auto" ? "clip-diagonal" : ""} />
      )}
    </span>
  );
}

const SAMPLE_SIZE: Record<string, string> = {
  system: "text-size-system",
  small: "text-size-small",
  medium: "text-size-medium",
  large: "text-size-large",
  larger: "text-size-larger",
};

/** "Aa" at the size the option gives, in a box that does not change with the size chosen. */
function TextPreview({ size }: { size: string }) {
  return (
    <span
      aria-hidden="true"
      className={`flex aspect-5/4 w-sample items-end justify-center font-semibold ${SAMPLE_SIZE[size] ?? ""}`}
    >
      Aa
    </span>
  );
}

/** Rows that are chosen by looking at a preview; any other row is a segmented control. */
const PREVIEWS: Partial<Record<SettingKey, (value: string) => ReactNode>> = {
  "ui.theme": (value) => <ThemePreview theme={value} />,
  "ui.text_size": (value) => <TextPreview size={value} />,
};

/**
 * The first-run choices, built from the settings table. Theme and text size apply as they are picked; Done, or any
 * other way of closing, stores the choices and marks Quick Setup done so it is not offered again. A refused write
 * keeps the live theme and text size preview, closes the dialog and goes to `onNotice`, which the shell shows as a dialog, without marking it done.
 */
export function QuickSetupDialog({
  onClose,
  onNotice,
}: {
  onClose: () => void;
  onNotice: (message: string) => void;
}) {
  const { values, savedValues, previewDevice } = useSettings();
  const [base] = useState(savedValues);
  const [opened] = useState(values);
  const [draft, setDraft] = useState(opened);
  const theme = values["ui.theme"];
  const textSize = values["ui.text_size"];
  useEffect(() => {
    setDraft((current) => ({ ...current, "ui.theme": theme, "ui.text_size": textSize }));
  }, [theme, textSize]);
  useAppearance(textOf(draft["ui.theme"]), textOf(draft["ui.text_size"]));
  const { saving, submit } = useSaveDialog(onClose);
  const done = async () => {
    const patch = settingsPatch(base, { ...draft, "ui.quick_setup_done": true }, opened);
    if (isEmpty(patch)) return onClose();
    const outcome = await submit(patch);
    if (outcome.kind === "refused") {
      onNotice(`Could not save these choices: ${reasonOf(outcome.refusal)}`);
      onClose();
    }
  };
  return (
    <Dialog title="Quick Setup" okLabel="Done" onOk={() => void done()} busy={saving}>
      <DialogBody>
        <fieldset
          disabled={saving}
          className="relative m-0 flex w-setup max-w-full shrink-0 min-w-0 flex-col gap-m border-0 p-0 [@media(max-height:20rem)]:gap-0 [&_[role=radiogroup]]:flex [&_[role=radiogroup]]:flex-wrap"
        >
          {ROWS.map((row) => {
            const choice = {
              label: row.label,
              options: row.kind.options ?? [],
              value: textOf(draft[row.key]),
              onChange: (value: string) => {
                if (row.scope === "device") previewDevice(row.key, value);
                setDraft({ ...draft, [row.key]: value });
              },
            };
            const preview = PREVIEWS[row.key];
            return (
              <section key={row.key} className="flex flex-col gap-s">
                <SectionCaption heading={row.label} plain />
                {preview ? (
                  <TileChoice {...choice} preview={preview} />
                ) : (
                  <SegmentedControl {...choice} wide />
                )}
              </section>
            );
          })}
        </fieldset>
        <p className="m-0 text-small text-muted">You can change these later in File → Settings.</p>
      </DialogBody>
    </Dialog>
  );
}
