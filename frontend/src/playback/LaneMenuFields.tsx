import { useId, useState, type ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { FIELD } from "../kit/look";
import { useSettings } from "../settings";

/** A number the user edits as text; the change applies when they press Enter or leave the field. */
export function NumberField({
  label,
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
  return (
    <div className={`flex items-center justify-between gap-s ${disabled ? "text-disabled" : ""}`}>
      <label htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
        {label}
      </label>
      <input
        id={id}
        type="number"
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
        className={`${FIELD} w-[10ch] text-right tabular-nums`}
      />
    </div>
  );
}

/** A set of related controls under a name. */
export function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex flex-col gap-s border-t border-line pt-m first:border-t-0 first:pt-0"
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
  const [refusal, setRefusal] = useState<string | null>(null);
  return (
    <div className="flex flex-col items-start gap-s">
      {isDefault ? (
        <span className="text-muted">Default for new views</span>
      ) : (
        <Button
          disabled={!usable}
          title="Start new viewers and lanes with these settings."
          onClick={() => {
            setRefusal(null);
            void set(setting, value).then((refused) => setRefusal(refused?.reason ?? null));
          }}
        >
          Make default
        </Button>
      )}
      {refusal && <span role="alert">{refusal}</span>}
    </div>
  );
}
