import { useEffect, useReducer, useRef } from "react";
import type { ApiError, DecoderInfo, FieldInfo, ResolverInfo } from "../generated/api";
import { Refusal } from "../shell/SettingField";
import { FieldControl, type FieldForm } from "./fields";
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

/** The fields' starting text: the saved settings, whatever else they hold kept, with each decoder choice filled in. */
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
    } else if (typeof values[field.key] !== "string") {
      values[field.key] = field.kind.kind === "Handler" ? (decoders[0]?.handler ?? "") : "";
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

/** What loading sends, or `null` while something is missing: a required text, or a scan with a checked value. */
function settingsOf(resolver: ResolverInfo, state: State): Settings | null {
  const settings: Settings = { ...state.values };
  for (const field of resolver.fields) {
    if (field.kind.kind === "Scan") {
      const view = state.scans[field.key];
      if (view?.phase !== "done" || checkedCount(view) === 0) return null;
      const names = chosen(view);
      if (names) settings[field.key] = names;
    } else if (blank(settings[field.key])) {
      if (field.required) return null;
      delete settings[field.key];
    }
  }
  return settings;
}

/** The refusals to show at `field`: the load's, and failed scans, each at the setting it names or else at its own field. */
function refusalsAt(
  resolver: ResolverInfo,
  scans: Readonly<Record<string, ScanView>>,
  refusal: ApiError | null,
  field: FieldInfo,
): ApiError[] {
  const names = (location: string) => resolver.fields.some((other) => other.key === location);
  const problems = [refusal, ...scanFields(resolver).map((scan) => scans[scan.key]?.failure)];
  return problems.filter(
    (problem): problem is ApiError =>
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
  const controllers = useRef(new Map<string, AbortController>());
  // Another resolver is another form.
  if (state.resolverId !== resolver.id) {
    dispatch({ type: "reset", state: start(resolver, decoders) });
  }

  const canScanWith = (key: string, values: Settings) => {
    const kind = resolver.fields.find((field) => field.key === key)?.kind;
    return kind?.kind === "Scan" && !kind.depends_on.some((need) => blank(values[need]));
  };
  const run = (key: string, values: Settings) => {
    controllers.current.get(key)?.abort();
    const controller = new AbortController();
    controllers.current.set(key, controller);
    dispatch({ type: "scan", key, update: scanStarted });
    scanChoices(resolver.id, key, values, controller.signal).then(
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
    values: state.values,
    scans: state.scans,
    settings: settingsOf(resolver, state),
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

/** The fields of a resolver, each with the refusals that are about it. `refusal` is the backend's answer to a load. */
export function ResolverForm({
  form,
  refusal,
}: {
  form: ResolverFormState;
  refusal: ApiError | null;
}) {
  return (
    <>
      {form.resolver.fields.map((field) => (
        <div key={field.key} className="flex flex-col gap-s">
          <FieldControl field={field} form={form} />
          {refusalsAt(form.resolver, form.scans, refusal, field).map((problem) => (
            <Refusal key={problem.reason} reason={problem.reason} />
          ))}
        </div>
      ))}
    </>
  );
}
