import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { FIELD } from "./look";
import { ownKeys } from "./keys";

const OWN = ownKeys(new Set(["Enter", "Escape"]));

/**
 * A heading that turns into a text field on click, Enter or Space. Every change is published through `onEdit` as it
 * is typed; Enter or leaving the field calls `onDone`, and Escape first restores the text the edit began with. A
 * `value` that changes during the edit is ignored, so typing is never overwritten. `label` names the field.
 */
export function EditableLabel({
  value,
  tooltip,
  label,
  onEdit,
  onDone,
}: {
  value: string;
  tooltip?: string;
  label: string;
  onEdit: (text: string) => void;
  onDone: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const entry = useRef("");
  const finished = useRef(true);
  const refocus = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    if (editing) {
      input.current?.focus();
      input.current?.select();
    } else if (refocus.current) {
      refocus.current = false;
      button.current?.focus();
    }
  }, [editing]);

  const finish = (restore: boolean, keepFocus: boolean) => {
    if (finished.current) return;
    finished.current = true;
    if (restore && draft !== entry.current) onEdit(entry.current);
    refocus.current = keepFocus;
    setEditing(false);
    onDone();
  };

  const onKeyDown = (event: KeyboardEvent) => {
    OWN(event);
    if (event.key === "Enter") finish(false, true);
    else if (event.key === "Escape") finish(true, true);
  };

  if (!editing)
    return (
      <button
        ref={button}
        type="button"
        title={tooltip ?? value}
        className="w-full min-w-0 cursor-text truncate rounded-control text-left focus-ring"
        onClick={() => {
          entry.current = value;
          finished.current = false;
          setDraft(value);
          setEditing(true);
        }}
      >
        {value}
      </button>
    );

  return (
    <input
      ref={input}
      type="text"
      aria-label={label}
      value={draft}
      className={`${FIELD} w-full min-w-0 font-semibold`}
      onChange={(event) => {
        setDraft(event.target.value);
        onEdit(event.target.value);
      }}
      onKeyDown={onKeyDown}
      onBlur={() => finish(false, false)}
    />
  );
}
