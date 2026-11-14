import { useEffect, useLayoutEffect, useReducer, useRef, type ReactNode } from "react";
import { reasonOf } from "../client/errors";
import type { Choice, DecoderInfo, ErrorWire, Field, ResolverInfo } from "../generated/api";
import { Refusal } from "../kit/Refusal";
import { decoderOptions, FieldControl, misread, needsValue, shown, type FieldForm } from "./fields";
import {
  checkedCount,
  chosen,
  idleScan,
  listed,
  scanChoices,
  scanFailed,
  scanFinished,
  scanInvalidated,
  scanStarted,
  toggled,
  toggledAll,
  type ScanView,
  type Settings,
} from "./scan";

interface State {
  values: Settings;
  scans: Record<string, ScanView>;
}

/** The form of each resolver the user has opened, by resolver id. */
type Forms = ReadonlyMap<string, State>;

type Action = { resolver: ResolverInfo } & (
  | { type: "set"; key: string; value: string; invalidated: readonly string[] }
  | { type: "scan"; key: string; update: (view: ScanView) => ScanView }
);

const blank = (value: unknown) => typeof value !== "string" || value.trim() === "";

const isScan = (field: Field) => field.kind.kind === "Scan";
const isCount = (field: Field) => field.kind.kind === "Scan" && !!field.kind.count;
/** The scans whose found values are checked, which gate loading and scan on their own. */
const scanFields = (resolver: ResolverInfo) =>
  resolver.fields.filter((field) => isScan(field) && !isCount(field));
/** The scans that only count, which gate loading only when they come up empty or fail. */
const countFields = (resolver: ResolverInfo) => resolver.fields.filter(isCount);

/**
 * The fields' starting text: the saved settings, whatever else they hold kept. A decoder choice starts blank unless
 * saved. The saved options of a decoder become the text of its option fields, under `<field key>.<option key>`.
 */
function start(resolver: ResolverInfo, initial?: Settings): State {
  const values = { ...initial };
  const scans: Record<string, ScanView> = {};
  for (const field of resolver.fields) {
    if (field.kind.kind === "Scan") {
      scans[field.key] = idleScan(values[field.key], initial !== undefined);
      delete values[field.key];
    } else if (field.kind.kind === "Integer" && typeof values[field.key] === "number") {
      values[field.key] = String(values[field.key]);
    } else if (field.kind.kind === "DecoderOptions") {
      const saved = values[field.key];
      delete values[field.key];
      if (saved && typeof saved === "object") {
        for (const [option, value] of Object.entries(saved))
          values[`${field.key}.${option}`] = String(value);
      }
    }
  }
  return { values, scans };
}

function reduce(forms: Forms, action: Action): Forms {
  const state = forms.get(action.resolver.id) ?? start(action.resolver);
  switch (action.type) {
    case "set":
      return new Map(forms).set(action.resolver.id, {
        values: { ...state.values, [action.key]: action.value },
        scans: {
          ...state.scans,
          ...Object.fromEntries(
            action.invalidated.map((key) => [key, scanInvalidated(state.scans[key])]),
          ),
        },
      });
    case "scan":
      return new Map(forms).set(action.resolver.id, {
        ...state,
        scans: { ...state.scans, [action.key]: action.update(state.scans[action.key]) },
      });
  }
}

/** The text of a field as it is sent: a number for an `Integer` field, a folder without its padding, nothing while blank. */
const typed = (field: Field, text: unknown) =>
  blank(text)
    ? undefined
    : field.kind.kind === "Integer"
      ? Number(text)
      : field.kind.kind === "Folder"
        ? String(text).trim()
        : text;

/**
 * The typed values the fields hold: defaults applied, integers as numbers, each decoder's options gathered under its
 * field, nothing for a blank or misread field. `complete` is false while a required field is missing or misread. Scan
 * fields are left out: their selections come from the scans.
 */
function effective(
  resolver: ResolverInfo,
  values: Settings,
  decoders: readonly DecoderInfo[],
): { settings: Settings; complete: boolean } {
  const settings: Settings = { ...values };
  let complete = true;
  for (const field of resolver.fields) {
    if (field.kind.kind === "Scan") continue;
    if (field.kind.kind === "DecoderOptions") {
      const options: Settings = {};
      for (const option of decoderOptions(field, values, decoders)) {
        const prefixed = { ...option, key: `${field.key}.${option.key}` };
        if (misread(prefixed, values)) {
          complete = false;
          continue;
        }
        const value = typed(option, shown(prefixed, values));
        if (value !== undefined) options[option.key] = value;
        else if (needsValue(option)) complete = false;
      }
      for (const key of Object.keys(settings)) {
        if (key.startsWith(`${field.key}.`)) delete settings[key];
      }
      if (Object.keys(options).length || needsValue(field)) settings[field.key] = options;
    } else {
      const value = misread(field, settings) ? undefined : typed(field, shown(field, settings));
      if (value !== undefined) settings[field.key] = value;
      else {
        if (misread(field, settings) || needsValue(field)) complete = false;
        delete settings[field.key];
      }
    }
  }
  return { settings, complete };
}

/**
 * What loading sends, or `null` while something is missing or misread: a required text without a default, a scan with
 * a checked value, or an integer that is not a whole number. `asked` leaves the counts out of it, which is what
 * loading sends once they come up with pairs.
 */
function settingsOf(
  resolver: ResolverInfo,
  state: State,
  decoders: readonly DecoderInfo[],
  asked = false,
): Settings | null {
  const { settings, complete } = effective(resolver, state.values, decoders);
  for (const field of asked ? [] : countFields(resolver)) {
    const view = state.scans[field.key];
    if (view?.phase === "scanning" || view?.phase === "failed") return null;
    if (view?.phase === "done" && view.choices.length === 0) return null;
  }
  for (const field of scanFields(resolver)) {
    const view = state.scans[field.key];
    // Nothing checked still loads: the dialog tells that it is nothing (`unchosen`) rather than disabling Load.
    if (view?.phase !== "done" || listed(view).length === 0) return null;
    const names = chosen(view);
    if (names) settings[field.key] = names;
  }
  return complete ? settings : null;
}

/** The message for a done scan that found values with none checked, else `null`. */
function unchosenOf(resolver: ResolverInfo, state: State): string | null {
  const empty = scanFields(resolver).find((field) => {
    const view = state.scans[field.key];
    return view?.phase === "done" && listed(view).length > 0 && checkedCount(view) === 0;
  });
  return empty ? `No ${empty.label.toLowerCase()} selected.` : null;
}

/** The failed scans to show at `field`, each at the setting it names or else at its own field. */
function failuresAt(
  resolver: ResolverInfo,
  scans: Readonly<Record<string, ScanView>>,
  field: Field,
): ErrorWire[] {
  const names = (location: string | undefined) =>
    resolver.fields.some((other) => other.key === location);
  return scanFields(resolver)
    .map((scan) => scans[scan.key]?.failure)
    .filter(
      (problem): problem is ErrorWire =>
        !!problem &&
        (problem.location === field.key ||
          (field.kind.kind === "Scan" &&
            scans[field.key]?.failure === problem &&
            !names(problem.location))),
    );
}

/** Whether two settings hold the same values, whatever order their keys were added in. */
export function sameSettings(a: Settings | null, b: Settings | null): boolean {
  const canonical = (settings: Settings | null) =>
    JSON.stringify(settings, (_key, value: unknown) =>
      value && typeof value === "object" && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).sort(([x], [y]) => (x < y ? -1 : 1)))
        : value,
    );
  return canonical(a) === canonical(b);
}

/** Whether everything the `Scan` field `key` reads is filled in, in `values`. */
function scanReady(
  resolver: ResolverInfo,
  decoders: readonly DecoderInfo[],
  key: string,
  values: Settings,
): boolean {
  const kind = resolver.fields.find((field) => field.key === key)?.kind;
  if (kind?.kind !== "Scan") return false;
  const { settings } = effective(resolver, values, decoders);
  // A blank setting with a default is met: the backend uses the default. A misread one is not.
  const unmet = (need: string) => {
    const field = resolver.fields.find((other) => other.key === need);
    return field && misread(field, values)
      ? true
      : settings[need] === undefined && field?.default == null;
  };
  return !kind.depends_on.some(unmet);
}

/** A resolver form's state and what to do with it. */
export interface ResolverFormState extends FieldForm {
  resolver: ResolverInfo;
  /** The form of each resolver opened so far, by resolver id, which `draftForm` shows. */
  forms: Forms;
  /** What loading sends, or `null` while the form cannot load. */
  settings: Settings | null;
  /**
   * Present while a count-only scan has not answered: scans, then gives `settings` if it found something and the form
   * holds the same settings as when asked, else `null`.
   */
  settingsAfterScan?: () => Promise<Settings | null>;
  /** Why loading must not go on although `settings` is complete: a scan found values and none is checked. */
  unchosen: string | null;
}

/**
 * The state of the form a resolver's schema declares. Each resolver the hook is given keeps its own form, so going
 * back to one finds it as it was. A `Scan` field scans when asked or when a picked path it reads changes; editing
 * anything it reads empties it and stops its scan, the newest scan alone fills it, and unmounting stops them all.
 * A count-only scan never starts by itself, and `settingsAfterScan` gives nothing if the form shows another resolver
 * or other settings when it answers (a change undone counts as none). `initial` holds the first resolver's saved
 * settings: unknown keys are kept, a saved list is the selection (saved
 * names the scan lacks stay, marked), and the fields scan at once.
 */
export function useResolverForm(
  resolver: ResolverInfo,
  decoders: readonly DecoderInfo[],
  initial?: Settings,
): ResolverFormState {
  const [forms, dispatch] = useReducer(
    reduce,
    undefined,
    (): Forms => new Map([[resolver.id, start(resolver, initial)]]),
  );
  // A resolver not opened yet starts blank; its form is stored with its first edit.
  const state = forms.get(resolver.id) ?? start(resolver);
  // Scans by `<resolver id>/<field key>`: a resolver left for another keeps scanning into its own form.
  const controllers = useRef(new Map<string, AbortController>());
  const slot = (key: string) => `${resolver.id}/${key}`;
  // The resolver and settings the form shows now, for a scan that was waited for to compare against.
  const latest = useRef<{ resolver: string; settings: Settings | null } | null>(null);

  const canScanWith = (key: string, values: Settings) => scanReady(resolver, decoders, key, values);
  const run = (key: string, values: Settings): Promise<readonly Choice[] | null> => {
    controllers.current.get(slot(key))?.abort();
    const controller = new AbortController();
    controllers.current.set(slot(key), controller);
    dispatch({ type: "scan", resolver, key, update: scanStarted });
    return scanChoices(
      resolver.id,
      key,
      effective(resolver, values, decoders).settings,
      controller.signal,
    ).then(
      (answer) => {
        // A superseded or cancelled scan changes nothing.
        if (controller.signal.aborted) return null;
        controllers.current.delete(slot(key));
        dispatch({
          type: "scan",
          resolver,
          key,
          update: (view) =>
            answer.ok ? scanFinished(view, answer.value) : scanFailed(view, answer.error),
        });
        return answer.ok ? answer.value : null;
      },
      () => null, // only an abort rejects
    );
  };
  const stop = (key: string) => {
    controllers.current.get(slot(key))?.abort();
    controllers.current.delete(slot(key));
  };

  useEffect(() => {
    if (initial) {
      for (const field of scanFields(resolver)) {
        if (canScanWith(field.key, state.values)) void run(field.key, state.values);
      }
    }
    // Leaving the form cancels what is still scanning.
    const running = controllers.current;
    return () => {
      for (const controller of running.values()) controller.abort();
      running.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once: the saved settings are only the start
  }, []);

  const settings = settingsOf(resolver, state, decoders);
  const asked = settingsOf(resolver, state, decoders, true);
  useLayoutEffect(() => {
    latest.current = { resolver: resolver.id, settings: asked };
  });
  const unanswered = countFields(resolver).filter(
    (field) => state.scans[field.key]?.phase !== "done",
  );
  const settingsAfterScan =
    settings && unanswered.length > 0
      ? async () => {
          const answers = await Promise.all(
            unanswered.map((field) =>
              canScanWith(field.key, state.values)
                ? run(field.key, state.values)
                : Promise.resolve(null),
            ),
          );
          return answers.every((found) => found?.length) &&
            latest.current?.resolver === resolver.id &&
            sameSettings(latest.current.settings, settings)
            ? settings
            : null;
        }
      : undefined;

  return {
    resolver,
    decoders,
    values: state.values,
    scans: state.scans,
    forms,
    settings,
    settingsAfterScan,
    unchosen: unchosenOf(resolver, state),
    canScan: (key) => canScanWith(key, state.values),
    scan: (key) => {
      if (canScanWith(key, state.values)) void run(key, state.values);
    },
    set: (key, value, commit = false) => {
      const values = { ...state.values, [key]: value };
      const dependants = resolver.fields.filter(
        (field) => field.kind.kind === "Scan" && field.kind.depends_on.includes(key),
      );
      const invalidated = dependants.map((field) => field.key);
      for (const dependant of invalidated) stop(dependant);
      dispatch({ type: "set", resolver, key, value, invalidated });
      if (commit) {
        for (const field of dependants.filter((dependant) => !isCount(dependant))) {
          if (canScanWith(field.key, values)) void run(field.key, values);
        }
      }
    },
    toggle: (key, value) =>
      dispatch({ type: "scan", resolver, key, update: (view) => toggled(view, value) }),
    toggleAll: (key) => dispatch({ type: "scan", resolver, key, update: toggledAll }),
  };
}

/**
 * `resolver`'s form as the person left it (blank if never opened), for a dialog that draws it out of sight to leave room
 * for it, decoder options and scan results included.
 */
export function draftForm(form: ResolverFormState, resolver: ResolverInfo): ResolverFormState {
  const state = form.forms.get(resolver.id) ?? start(resolver);
  return {
    ...form,
    resolver,
    ...state,
    // What the draft shows is its own; the dialog keeps the page out of reach (`inert`), so nothing here is run.
    settings: settingsOf(resolver, state, form.decoders),
    settingsAfterScan: undefined,
    unchosen: unchosenOf(resolver, state),
    canScan: (key) => scanReady(resolver, form.decoders, key, state.values),
  };
}

/**
 * The fields of a resolver, each with the failed scans that are about it. `action` is the dialog's own button: it
 * sits beside the Scan button of a count-only scan, or else closes the form on a row of its own.
 */
export function ResolverForm({ form, action }: { form: ResolverFormState; action?: ReactNode }) {
  const fieldForm = { ...form, action };
  return (
    <>
      {form.resolver.fields.map((field) => (
        <div key={field.key} className="flex flex-col gap-s">
          <FieldControl field={field} form={fieldForm} />
          {failuresAt(form.resolver, form.scans, field).map((problem) => (
            <Refusal key={reasonOf(problem)} reason={reasonOf(problem)} />
          ))}
        </div>
      ))}
      {action && countFields(form.resolver).length === 0 && (
        <div className="flex justify-end">{action}</div>
      )}
    </>
  );
}
