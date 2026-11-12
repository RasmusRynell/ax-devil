import { useId, type ReactNode } from "react";
import { IconButton } from "../kit/IconButton";
import { FIELD } from "../kit/look";
import { ownKeys } from "../kit/keys";
import { numericInput } from "./numericInput";

/** The dialog's last Manual text and exact MiB, retained while other pages are mounted. */
export interface ManualCache {
  text: string;
  mib: number | string;
}

/** Cache allowance displayed in GiB; untouched rounded text retains the original stored MiB. */
export function VideoCacheField({
  label,
  value,
  onChange,
  manual,
  onManualChange,
}: {
  label: ReactNode;
  value: string | number;
  onChange: (value: string | number) => void;
  manual: ManualCache;
  onManualChange: (value: ManualCache) => void;
}) {
  const id = useId();
  const auto = value === "auto";
  const parsed = numericInput(manual.text, "auto_or_whole");
  const edit = (text: string) => {
    const number = numericInput(text, "auto_or_whole");
    const mib = typeof number === "number" ? Math.round(Number(number.toFixed(2)) * 1024) : text;
    onManualChange({ text, mib });
    onChange(mib);
  };
  const step = (direction: number) => {
    if (typeof parsed === "number")
      edit(Math.max(0.25, Math.min(1024, parsed + direction * 0.25)).toFixed(2));
  };
  return (
    <div className="flex min-w-0 flex-col gap-s">
      <label htmlFor={id}>{label}</label>
      <div className="flex min-w-0 items-center gap-s">
        <select
          id={id}
          className={`${FIELD} min-w-0`}
          value={auto ? "auto" : "manual"}
          title="Auto uses 25% of available RAM"
          onChange={(event) =>
            event.target.value === "auto" ? onChange("auto") : onChange(manual.mib)
          }
        >
          <option value="auto">Auto</option>
          <option value="manual">Manual</option>
        </select>
        {!auto && (
          <>
            <input
              type="text"
              role="spinbutton"
              inputMode="decimal"
              aria-label="Total allowance (GiB)"
              aria-valuemin={0.25}
              aria-valuemax={1024}
              aria-valuenow={typeof parsed === "number" ? parsed : undefined}
              value={manual.text}
              className={`${FIELD} min-w-0 flex-1 tabular-nums`}
              onChange={(event) => edit(event.target.value)}
              onBlur={() => {
                if (typeof parsed === "number")
                  onManualChange({ ...manual, text: parsed.toFixed(2) });
              }}
              onKeyDown={(event) => {
                ownKeys(new Set(["ArrowUp", "ArrowDown"]))(event);
                if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                event.preventDefault();
                step(event.key === "ArrowUp" ? 1 : -1);
              }}
            />
            <span>GiB</span>
            <IconButton
              icon="minus"
              label="Decrease allowance"
              onClick={() => step(-1)}
              disabled={typeof parsed !== "number" || parsed <= 0.25}
            />
            <IconButton
              icon="plus"
              label="Increase allowance"
              onClick={() => step(1)}
              disabled={typeof parsed !== "number" || parsed >= 1024}
            />
          </>
        )}
      </div>
    </div>
  );
}
