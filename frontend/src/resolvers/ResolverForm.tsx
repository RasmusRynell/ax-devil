import { useEffect, useReducer, useRef } from "react";
import { reasonOf } from "../errors";
import type { DecoderInfo, ErrorWire, FieldInfo, ResolverInfo } from "../generated/api";
import { Refusal } from "../shell/SettingField";
import { useServerKind } from "../shell/windowBridge";
import { decoderOptions, FieldControl, misread, needsValue, shown, type FieldForm } from "./fields";
import {
  checkedCount,
  chosen,
  idleScan,
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
  resolverId: string;
  values: Settings;
  scans: Record<string, ScanView>;
}

type Action =
  | { type: "reset"; state: State }
  | { type: "set"; key: string; value: string; invalidated: readonly string[] }
  | { type: "scan"; key: string; update: (view: ScanView) => ScanView };

const blank = (value: unknown) => typeof value !== "string" || value.trim() === "";

const scanFields = (resolver: ResolverInfo) =>
  resolver.fields.filter((field) => field.kind.kind === "Scan");

/**
 * The fields' starting text: the saved settings, whatever else they hold kept, with each decoder choice filled in. The
 * saved options of a decoder become the text of its option fields, under `<field key>.<option key>`.
 */
function start(
  resolver: ResolverInfo,
  decoders: readonly DecoderInfo[],
  initial: Settings = {},
): State {
  const values = { ...initial };
  const scans: Record<string, ScanView> = {};
  for (const field of resolver.fields) {
    if (field.kind.kind === "Scan") {
      scans[field.key] = idleScan(values[field.key]);
      delete values[field.key];
    } else if (field.kind.kind === "DecoderOptions") {
      const saved = values[field.key];
      delete values[field.key];
      if (saved && typeof saved === "object") {
        for (const [option, value] of Object.entries(saved))
          values[`${field.key}.${option}`] = String(value);
      }
    } else if (field.kind.kind === "Handler" && typeof values[field.key] !== "string") {
      values[field.key] = decoders[0]?.handler ?? "";
    }
  }
  return { resolverId: resolver.id, values, scans };
}

function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return action.state;
    case "set":
      return {
        ...state,
        values: { ...state.values, [action.key]: action.value },
        scans: {
          ...state.scans,
          ...Object.fromEntries(
            action.invalidated.map((key) => [key, scanInvalidated(state.scans[key])]),
          ),
        },
      };
    case "scan":
      // A scan of a resolver left a moment ago may still land.
      if (!state.scans[action.key]) return state;
      return {
        ...state,
        scans: { ...state.scans, [action.key]: action.update(state.scans[action.key]) },
      };
  }
}

/** The text of a field as it is sent: a number for an `Integer` field, nothing while blank. */
const typed = (field: FieldInfo, text: unknown) =>
  blank(text) ? undefined : field.kind.kind === "Integer" ? Number(text) : text;

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

/** What loading sends, or `null` while something is missing or misread: a required text without a default, a scan with a checked value, or an integer that is not a whole number. */
function settingsOf(
  resolver: ResolverInfo,
  state: State,
  decoders: readonly DecoderInfo[],
): Settings | null {
  const { settings, complete } = effective(resolver, state.values, decoders);
  for (const field of scanFields(resolver)) {
    const view = state.scans[field.key];
    if (view?.phase !== "done" || checkedCount(view) === 0) return null;
    const names = chosen(view);
    if (names) settings[field.key] = names;
  }
  return complete ? settings : null;
}

/** The failed scans to show at `field`, each at the setting it names or else at its own field. */
function failuresAt(
  resolver: ResolverInfo,
  scans: Readonly<Record<string, ScanView>>,
  field: FieldInfo,
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

/** A resolver form's state and what to do with it. */
export interface ResolverFormState extends FieldForm {
  resolver: ResolverInfo;
  /** What loading sends, or `null` while the form cannot load. */
  settings: Settings | null;
}

/**
 * The state of the form a resolver's schema declares. A `Scan` field scans when asked or when a picked path it reads
 * changes; editing anything it reads empties it and stops its scan, the newest scan alone fills it, and leaving
 * stops them all. `initial` holds saved settings: unknown keys are kept, a saved list is the selection (saved
 * names the scan lacks stay, marked), and the fields scan at once.
 */
export function useResolverForm(
  resolver: ResolverInfo,
  decoders: readonly DecoderInfo[],
  initial?: Settings,
): ResolverFormState {
  const [state, dispatch] = useReducer(reduce, undefined, () => start(resolver, decoders, initial));
  // Only the backend's own window shares its machine's folders with it.
  const bridge = useServerKind() === "local" ? window.axDevilWindow : undefined;
  const controllers = useRef(new Map<string, AbortController>());
  // Another resolver is another form.
  if (state.resolverId !== resolver.id) {
    dispatch({ type: "reset", state: start(resolver, decoders) });
  }

  const canScanWith = (key: string, values: Settings) => {
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
  };
  const run = (key: string, values: Settings) => {
    controllers.current.get(key)?.abort();
    const controller = new AbortController();
    controllers.current.set(key, controller);
    dispatch({ type: "scan", key, update: scanStarted });
    scanChoices(
      resolver.id,
      key,
      effective(resolver, values, decoders).settings,
      controller.signal,
    ).then(
      (answer) => {
        // A superseded or cancelled scan changes nothing.
        if (controller.signal.aborted) return;
        controllers.current.delete(key);
        dispatch({
          type: "scan",
          key,
          update: (view) =>
            answer.ok ? scanFinished(view, answer.value) : scanFailed(view, answer.refusal),
        });
      },
      () => undefined, // only an abort rejects
    );
  };
  const stop = (key: string) => {
    controllers.current.get(key)?.abort();
    controllers.current.delete(key);
  };

  useEffect(() => {
    if (initial) {
      for (const field of scanFields(resolver)) {
        if (canScanWith(field.key, state.values)) run(field.key, state.values);
      }
    }
    // Leaving the form, or the resolver, cancels what is still scanning.
    const running = controllers.current;
    return () => {
      for (const controller of running.values()) controller.abort();
      running.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per resolver: the saved settings are only the start
  }, [resolver.id]);

  return {
    resolver,
    decoders,
    pickFolder: bridge && (() => bridge.pickFolder()),
    values: state.values,
    scans: state.scans,
    settings: settingsOf(resolver, state, decoders),
    canScan: (key) => canScanWith(key, state.values),
    scan: (key) => run(key, state.values),
    set: (key, value, commit = false) => {
      const values = { ...state.values, [key]: value };
      const invalidated = scanFields(resolver)
        .filter((field) => field.kind.kind === "Scan" && field.kind.depends_on.includes(key))
        .map((field) => field.key);
      for (const dependant of invalidated) stop(dependant);
      dispatch({ type: "set", key, value, invalidated });
      if (commit) {
        for (const dependant of invalidated) {
          if (canScanWith(dependant, values)) run(dependant, values);
        }
      }
    },
    toggle: (key, value) => dispatch({ type: "scan", key, update: (view) => toggled(view, value) }),
    toggleAll: (key) => dispatch({ type: "scan", key, update: toggledAll }),
  };
}

/** The fields of a resolver, each with the failed scans that are about it. */
export function ResolverForm({ form }: { form: ResolverFormState }) {
  return (
    <>
      {form.resolver.fields.map((field) => (
        <div key={field.key} className="flex flex-col gap-s">
          <FieldControl field={field} form={form} />
          {failuresAt(form.resolver, form.scans, field).map((problem) => (
            <Refusal key={reasonOf(problem)} reason={reasonOf(problem)} />
          ))}
        </div>
      ))}
    </>
  );
}
