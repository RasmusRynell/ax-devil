import { useId, useState, type ReactNode } from "react";
import { useAccess } from "../client/access";
import { reasonOf } from "../client/errors";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { FormRow } from "../kit/Form";
import { FIELD } from "../kit/look";
import { useSettings } from "../client/settings";

/** A number the user edits as text; the change applies when they press Enter or leave the field. */
export function NumberField({
  label,
  help,
  value,
  min,
  max,
  step,
  placeholder,
  disabled = false,
  hideLabel = false,
  onCommit,
}: {
  label: string;
  /** Help announced when this editor receives focus. */
  help?: string;
  value: number | null;
  min: number;
  max: number;
  step: number;
  /** What an empty field means, when it can be empty. */
  placeholder?: string;
  disabled?: boolean;
  /** Another control beside the field already says what it is for; the label then only names it for assistive technology. */
  hideLabel?: boolean;
  onCommit: (value: number | null) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  // The field holds no finer a fraction than its step does: whole milliseconds, opacity in hundredths.
  const decimals = (String(step).split(".")[1] ?? "").length;
  const commit = () => {
    if (draft === null) return;
    setDraft(null);
    const typed = draft.trim() === "" ? null : Number(draft);
    if (typed === null && placeholder === undefined) return;
    if (typed !== null && !Number.isFinite(typed)) return;
    const next =
      typed === null ? null : Number(Math.min(max, Math.max(min, typed)).toFixed(decimals));
    if (next !== value) onCommit(next);
  };
  const field = (
    <input
      id={id}
      type="number"
      title={help}
      aria-description={help}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      disabled={disabled}
      value={
        draft ?? (value === null ? "" : decimals > 0 ? value.toFixed(decimals) : String(value))
      }
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => event.key === "Enter" && commit()}
      className={`${FIELD} min-w-0 flex-1 tabular-nums`}
    />
  );
  return hideLabel ? (
    <div className="flex min-w-0 items-center">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      {field}
    </div>
  ) : (
    <FormRow label={label} id={id}>
      {field}
    </FormRow>
  );
}

/** A set of related rows under a name, sharing the columns of the `Form` around it. */
export function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="col-span-full mt-s grid grid-cols-subgrid gap-y-s border-t border-line pt-m first:mt-0 first:border-t-0 first:pt-0"
    >
      {children}
    </div>
  );
}

/**
 * Saves the lane's `value` as the Personal setting new views start with. While the lane already has the saved
 * default it says so instead; a refusal shows beside the button. `usable` is false when the lane's value cannot be a
 * default.
 */
export function MakeDefault({
  setting,
  value,
  isDefault,
  usable = true,
}: {
  setting: SettingKey;
  value: SettingsState["values"][SettingKey];
  isDefault: boolean;
  usable?: boolean;
}) {
  const { set } = useSettings();
  const { owner } = useAccess();
  const [refusal, setRefusal] = useState<string | null>(null);
  // The defaults are the Owner's to change.
  if (!owner) return null;
  return (
    <div className="col-span-full flex flex-col items-start gap-s">
      {isDefault ? (
        <span className="text-muted">Default for new views</span>
      ) : (
        <Button
          disabled={!usable}
          title="Start new viewers and lanes with these settings."
          onClick={() => {
            setRefusal(null);
            void set(setting, value).then((refused) =>
              setRefusal(refused ? reasonOf(refused) : null),
            );
          }}
        >
          Make default
        </Button>
      )}
      {refusal && <span role="alert">{refusal}</span>}
    </div>
  );
}
