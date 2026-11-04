import { useState } from "react";
import {
  compileFilter,
  MEASURES,
  OPEN_RANGES,
  withRanges,
  type Diagnostic,
  type Measure,
  type MeasureRanges,
} from "../present";
import type { FilterOptionInfo } from "../generated/api";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import { FIELD } from "../kit/look";
import type { LaneSettings, LaneState } from "./lanes";
import { Group } from "./LaneMenuFields";

type Change = (change: Partial<LaneSettings>) => void;

/** The classes the lane's file calls for; none while the lane has no file status yet. */
function classOptions(lane: LaneState): readonly FilterOptionInfo[] {
  return lane.status === "plain" || lane.status.status !== "Ready" ? [] : lane.status.filter;
}

/** Whether a measure has a bound, which is when its range differs from the default. */
const bounded = (ranges: MeasureRanges, measure: Measure): boolean =>
  ranges[measure].min !== null || ranges[measure].max !== null;

/** How many controls differ from their defaults: switched classes, measures with a bound, and a non-empty expression. */
export function changedControls(lane: LaneState): number {
  const { filter, ranges, expression } = lane.settings;
  const classes = classOptions(lane).filter(
    (option) => (filter[option.id] ?? option.default_enabled) !== option.default_enabled,
  ).length;
  return (
    classes + MEASURES.filter((measure) => bounded(ranges, measure)).length + (expression ? 1 : 0)
  );
}

/** The classes the lane's file calls for, with the buttons that switch them all on or all off. */
function Classes({
  options,
  filter,
  onChange,
}: {
  options: readonly FilterOptionInfo[];
  filter: LaneSettings["filter"];
  onChange: Change;
}) {
  if (!options.length) return null;
  const isOn = (option: FilterOptionInfo) => filter[option.id] ?? option.default_enabled;
  const setAll = (on: boolean) =>
    onChange({ filter: Object.fromEntries(options.map((option) => [option.id, on])) });
  return (
    <Group label="Classes">
      <div className="flex items-center justify-between gap-xl">
        <span>Classes</span>
        <div className="flex gap-s">
          <Button disabled={options.every(isOn)} onClick={() => setAll(true)}>
            Show all
          </Button>
          <Button disabled={!options.some(isOn)} onClick={() => setAll(false)}>
            None
          </Button>
        </div>
      </div>
      {options.map((option) => (
        <Checkbox
          key={option.id}
          label={option.label}
          checked={isOn(option)}
          onChange={(on) => onChange({ filter: { ...filter, [option.id]: on } })}
        />
      ))}
    </Group>
  );
}

/**
 * What is wrong with the lane's filter once the authored `expression` and the `ranges` are one expression: the
 * expression's own problems, else those of the whole (which has a length limit). None when it compiles.
 */
function problems(expression: string, ranges: MeasureRanges): readonly Diagnostic[] {
  const own = compileFilter(expression);
  if (!own.ok) return own.diagnostics;
  const whole = compileFilter(withRanges(expression, ranges));
  return whole.ok ? [] : whole.diagnostics;
}

/** One message each, announced as an alert. */
function Alerts({ found }: { found: readonly Diagnostic[] }) {
  return found.map((diagnostic, at) => (
    <span key={at} role="alert" className="text-small break-words">
      {diagnostic.message}
    </span>
  ));
}

/** The bound typed as text, `null` when empty, `undefined` when it is not a finite number. A comma is a decimal point. */
function parseBound(text: string): number | null | undefined {
  const trimmed = text.trim().replaceAll(",", ".");
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : undefined;
}

const ENDS = ["min", "max"] as const;

const NAMES: Record<Measure, string> = { score: "Score", confidence: "Confidence" };

/**
 * The smallest and largest score and confidence to keep; an empty end is open. Each field is checked at every
 * keystroke and applied on Enter or when it is left, and only if it is a number and the lane's filter still compiles.
 */
function Ranges({
  expression,
  ranges,
  onChange,
}: {
  expression: string;
  ranges: MeasureRanges;
  onChange: Change;
}) {
  const [drafts, setDrafts] = useState<Readonly<Record<string, string>>>({});
  const field = (measure: Measure, end: (typeof ENDS)[number]) => {
    const name = `${measure}.${end}`;
    const current = ranges[measure][end];
    const draft = drafts[name];
    const text = draft ?? (current === null ? "" : String(current));
    const value = parseBound(text);
    const next = (): MeasureRanges => ({
      ...ranges,
      [measure]: { ...ranges[measure], [end]: value },
    });
    const found = value === undefined ? [] : problems(expression, next());
    const apply = () => {
      if (draft === undefined || value === undefined || found.length > 0) return;
      setDrafts(Object.fromEntries(Object.entries(drafts).filter(([key]) => key !== name)));
      if (value !== current) onChange({ ranges: next() });
    };
    return { name, text, invalid: value === undefined || found.length > 0, found, apply };
  };
  const fields = MEASURES.map((measure) => ({
    measure,
    ends: ENDS.map((end) => field(measure, end)),
  }));
  return (
    <Group label="Ranges">
      {fields.map(({ measure, ends }) => (
        <div key={measure} className="flex items-center justify-between gap-xl">
          <span>{NAMES[measure]}</span>
          <div className="flex gap-s">
            {ends.map(({ name, text, invalid, apply }, at) => (
              <input
                key={name}
                type="text"
                inputMode="decimal"
                value={text}
                placeholder={ENDS[at]}
                aria-label={`${NAMES[measure]} ${ENDS[at]}`}
                aria-invalid={invalid}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setDrafts({ ...drafts, [name]: event.target.value })}
                onBlur={apply}
                onKeyDown={(event) => event.key === "Enter" && apply()}
                className={`${FIELD} w-[6ch] min-w-0 text-right tabular-nums`}
              />
            ))}
          </div>
        </div>
      ))}
      <Alerts found={fields.flatMap(({ ends }) => ends.flatMap(({ found }) => found))} />
    </Group>
  );
}

/**
 * The expression that keeps an object when it is true, such as `score >= 0.4`. It is checked at every keystroke and
 * applied on Enter or when the field is left, and only if it compiles together with the ranges: a lane never runs
 * an expression that does not.
 */
function Expression({
  expression,
  ranges,
  onChange,
}: {
  expression: string;
  ranges: MeasureRanges;
  onChange: Change;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? expression;
  const found = problems(text, ranges);
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
          className={`${FIELD} min-w-0`}
        />
      </label>
      <Alerts found={found} />
    </Group>
  );
}

/** Which objects the lane shows: by class, by score and confidence range, and by an expression over their fields. */
export function LaneMenuFilter({ lane, onChange }: { lane: LaneState; onChange: Change }) {
  const { filter, ranges, expression } = lane.settings;
  // Resetting remounts the fields, which drops what was typed and not applied.
  const [round, setRound] = useState(0);
  const differs =
    Object.keys(filter).length > 0 || MEASURES.some((m) => bounded(ranges, m)) || expression !== "";
  return (
    <>
      <Classes options={classOptions(lane)} filter={filter} onChange={onChange} />
      <Ranges key={`ranges${round}`} expression={expression} ranges={ranges} onChange={onChange} />
      <Expression
        key={`expression${round}`}
        expression={expression}
        ranges={ranges}
        onChange={onChange}
      />
      <div className="flex justify-end">
        <Button
          disabled={!differs}
          onClick={() => {
            setRound(round + 1);
            onChange({ filter: {}, ranges: OPEN_RANGES, expression: "" });
          }}
        >
          Reset
        </Button>
      </div>
    </>
  );
}
