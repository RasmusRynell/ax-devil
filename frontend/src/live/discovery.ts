/**
 * What the Add Live Stream form asks a camera: the stream sizes of a head, and the analytics sources or DataHub topics.
 * A question is the backend's, which expands `$NAME` references as a stream item does.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ask, type Answer } from "../client/ask";
import { reasonOf } from "../client/errors";
import type {
  ErrorCode,
  ErrorWire,
  LiveConnection,
  LiveResolutions,
  ResolutionsRequest,
  SourcesAnswer,
  SourcesOverlay,
  SourcesRequest,
} from "../generated/api";
import { reasonText } from "./words";

/** How long the connection stays as typed before the camera is asked about it. */
export const DEBOUNCE_MS = 600;

/** The stream sizes of every head of the camera in `connection`; `connection.head` plays no part. */
export function askResolutions(
  connection: LiveConnection,
  signal: AbortSignal,
): Promise<Answer<LiveResolutions>> {
  const body: ResolutionsRequest = { connection };
  return ask("/api/live/resolutions", { json: body, signal });
}

/** The `WIDTHxHEIGHT` sizes `head` offers: its own list, else the camera-wide one; none for a head below 1. */
export function sizesOf(answer: LiveResolutions, head: number): string[] {
  if (head < 1) return [];
  const own = answer.heads.find((listed) => listed.head === head);
  return (own ? own.resolutions : answer.common).map(({ width, height }) => `${width}x${height}`);
}

/** The analytics data sources (MQTT) or the DataHub topics of the camera in `connection`. */
export function askSources(
  connection: LiveConnection,
  overlay: SourcesOverlay,
  signal: AbortSignal,
): Promise<Answer<SourcesAnswer>> {
  const body: SourcesRequest = { connection, overlay };
  return ask("/api/live/sources", { json: body, signal });
}

/** Where a question stands. */
export type Discovery<T> =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; value: T }
  /** The form holds something the backend refuses; `error.location` names the field. */
  | { phase: "invalid"; error: ErrorWire }
  | { phase: "failed"; text: string };

const IDLE = { phase: "idle" } as const;

/** The codes live intake gives to what the form holds (a field, or the stream once a reference expands to nothing). */
const FORM_ERRORS = new Set<ErrorCode>(["WorkspaceInvalid", "UnknownHandler"]);

function stateOf<T>(answer: Answer<T>): Discovery<T> {
  if (answer.ok) return { phase: "ready", value: answer.value };
  const { error } = answer;
  if (FORM_ERRORS.has(error.code)) return { phase: "invalid", error };
  const camera = error.params.camera;
  return { phase: "failed", text: camera ? reasonText(camera) : reasonOf(error) };
}

/** What a question gives the form. */
export interface Asked<T> {
  state: Discovery<T>;
  /** Asks again now. */
  reload: () => void;
}

/**
 * Asks `question` whenever `key` changes, `delayMs` after the last change; `question` of `null` is nothing to ask. A
 * change drops the question in flight and its answer, so an answer is never shown for a form that has moved on. The
 * state is `loading` from the change on.
 */
export function useDiscovery<T>(
  question: ((signal: AbortSignal) => Promise<Answer<T>>) | null,
  key: string,
  delayMs: number,
): Asked<T> {
  const [state, setState] = useState<Discovery<T>>(IDLE);
  const [round, setRound] = useState(0);
  const latest = useRef(question);
  // A question the user asked for (Refresh) waits for nothing.
  const asked = useRef(false);
  useEffect(() => {
    latest.current = question;
  });
  const asking = question !== null;

  useEffect(() => {
    const run = latest.current;
    if (!asking || !run) {
      setState(IDLE);
      return;
    }
    setState({ phase: "loading" });
    const wait = asked.current ? 0 : delayMs;
    asked.current = false;
    const request = new AbortController();
    const timer = setTimeout(() => {
      run(request.signal).then(
        (answer) => {
          if (!request.signal.aborted) setState(stateOf(answer));
        },
        () => undefined, // aborted: the form moved on
      );
    }, wait);
    return () => {
      clearTimeout(timer);
      request.abort();
    };
  }, [asking, key, round, delayMs]);

  const reload = useCallback(() => {
    asked.current = true;
    setRound((now) => now + 1);
  }, []);
  return { state, reload };
}
