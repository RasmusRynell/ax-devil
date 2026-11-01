import { useState } from "react";
import { compileFilter, type Diagnostic } from "../present";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import type { LaneSettings, LaneState } from "./lanes";
import { Group } from "./LaneMenuFields";

/** The classes the lane's file calls for, with the buttons that switch them all on or back to the defaults. */
function Classes({
  lane,
  onChange,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  if (lane.status === "plain" || lane.status.status !== "Ready" || !lane.status.filter.length)
    return null;
  const options = lane.status.filter;
  const { filter } = lane.settings;
  const isOn = (id: string, fallback: boolean) => filter[id] ?? fallback;
  return (
    <Group label="Classes">
      <div className="flex items-center justify-between gap-xl">
        <span>Classes</span>
        <div className="flex gap-s">
          <Button
            disabled={options.every((option) => isOn(option.id, option.default_enabled))}
            onClick={() =>
              onChange({ filter: Object.fromEntries(options.map((option) => [option.id, true])) })
            }
          >
            Show all
          </Button>
          <Button
            disabled={Object.keys(filter).length === 0}
            onClick={() => onChange({ filter: {} })}
          >
            Reset
          </Button>
        </div>
      </div>
      {options.map((option) => (
        <Checkbox
          key={option.id}
          label={option.label}
          checked={isOn(option.id, option.default_enabled)}
          onChange={(on) => onChange({ filter: { ...filter, [option.id]: on } })}
        />
      ))}
    </Group>
  );
}

/** What is wrong with a filter expression, one message each; none when it compiles. */
function problems(expression: string): readonly Diagnostic[] {
  const result = compileFilter(expression);
  return result.ok ? [] : result.diagnostics;
}

/**
 * The expression that keeps an object when it is true, such as `score >= 0.4`. It is checked at every keystroke and
 * applied on Enter or when the field is left, and only if it compiles: a lane never runs an expression that does not.
 */
function Expression({
  expression,
  onChange,
}: {
  expression: string;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? expression;
  const found = problems(text);
  const apply = () => {
    if (draft === null || found.length > 0) return;
    setDraft(null);
    if (draft !== expression) onChange({ expression: draft });
  };
  return (
    <Group label="Filter expression">
      <label className="flex flex-col gap-s">
        Filter expression
        <input
          type="text"
          value={text}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={found.length > 0}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={apply}
          onKeyDown={(event) => event.key === "Enter" && apply()}
          className="h-row min-w-0 rounded-control border border-line-strong bg-popup px-s text-fg focus:border-accent focus:outline-none"
        />
      </label>
      {found.map((diagnostic, at) => (
        <span key={at} role="alert" className="text-small break-words">
          {diagnostic.message}
        </span>
      ))}
    </Group>
  );
}

/** Which objects the lane shows: by class, and by an expression over their fields. */
export function LaneMenuFilter({
  lane,
  onChange,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  return (
    <>
      <Classes lane={lane} onChange={onChange} />
      <Expression expression={lane.settings.expression} onChange={onChange} />
    </>
  );
}
