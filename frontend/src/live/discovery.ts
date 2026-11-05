/**
 * What the Add Live Stream form asks a camera: the stream sizes of a head, and the analytics sources or DataHub topics.
 * A question is the backend's, which expands `$NAME` references under the rule of a stream item; a reference that
 * would go to a host the user has not allowed comes back as a confirmation to ask about.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../api";
import type {
  ErrorWire,
  LiveConfirmation,
  LiveConnection,
  LiveProblem,
  LiveResolutions,
  ResolutionsRequest,
  SourcesAnswer,
  SourcesOverlay,
  SourcesRequest,
} from "../generated/api";
import { reasonText } from "./words";

/** How long the connection stays as typed before the camera is asked about it. */
export const DEBOUNCE_MS = 600;

/** Why a question got no answer: the backend's problem, or the words for a backend that could not be reached. */
export type Trouble = LiveProblem | { problem: "Backend"; text: string };

/** What a question came back with. */
export type Reply<T> = { ok: true; value: T } | { ok: false; trouble: Trouble };

async function post<T>(url: string, body: unknown, signal?: AbortSignal): Promise<Reply<T>> {
  try {
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (response.ok) {
      return {
        ok: true,
        value: (response.status === 204 ? undefined : await response.json()) as T,
      };
    }
    const problem = (await response.json().catch(() => null)) as LiveProblem | null;
    return problem?.problem
      ? { ok: false, trouble: problem }
      : {
          ok: false,
          trouble: { problem: "Backend", text: `The backend answered ${response.status}` },
        };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    const text = error instanceof Error ? error.message : String(error);
    return { ok: false, trouble: { problem: "Backend", text } };
  }
}

/** The stream sizes of every head of the camera in `connection`; `connection.head` plays no part. */
export function askResolutions(
  connection: LiveConnection,
  signal: AbortSignal,
): Promise<Reply<LiveResolutions>> {
  const body: ResolutionsRequest = { connection };
  return post("/api/live/resolutions", body, signal);
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
): Promise<Reply<SourcesAnswer>> {
  const body: SourcesRequest = { connection, overlay };
  return post("/api/live/sources", body, signal);
}

/** Where a question stands. */
export type Discovery<T> =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; value: T }
  /** The form holds something the backend refuses; `error.location` names the field. */
  | { phase: "invalid"; error: ErrorWire }
  /** A `$NAME` reference would go to a host the user has not allowed. */
  | { phase: "confirm"; confirmation: LiveConfirmation }
  | { phase: "failed"; text: string };

const IDLE = { phase: "idle" } as const;

function stateOf<T>(reply: Reply<T>): Discovery<T> {
  if (reply.ok) return { phase: "ready", value: reply.value };
  const { trouble } = reply;
  switch (trouble.problem) {
    case "Invalid":
      return { phase: "invalid", error: trouble.error };
    case "NeedsConfirmation":
      return { phase: "confirm", confirmation: trouble.confirmation };
    case "Camera":
      return { phase: "failed", text: reasonText(trouble.reason) };
    case "Backend":
      return { phase: "failed", text: trouble.text };
  }
}

/** What a question gives the form. */
export interface Asked<T> {
  state: Discovery<T>;
  /** Asks again now. */
  reload: () => void;
  /** Lets the pending reference go to its host, then asks again. */
  confirm: () => void;
}

/**
 * Asks `ask` whenever `key` changes, `delayMs` after the last change; `ask` of `null` is nothing to ask. A change
 * drops the question in flight and its answer, so an answer is never shown for a form that has moved on. The state
 * is `loading` from the change on.
 */
export function useDiscovery<T>(
  ask: ((signal: AbortSignal) => Promise<Reply<T>>) | null,
  key: string,
  delayMs: number,
): Asked<T> {
  const [state, setState] = useState<Discovery<T>>(IDLE);
  const [round, setRound] = useState(0);
  const latest = useRef(ask);
  // A question the user asked for (Refresh, Confirm) waits for nothing.
  const asked = useRef(false);
  // Counts the questions begun (a change of `key`, a reload); a confirmation answer from an earlier one is stale.
  const generation = useRef(0);
  useEffect(() => {
    latest.current = ask;
  });
  const asking = ask !== null;

  useEffect(() => {
    generation.current++;
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
        (reply) => {
          if (!request.signal.aborted) setState(stateOf(reply));
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
  const confirm = useCallback(() => {
    if (state.phase !== "confirm") return;
    const begun = generation.current;
    void post("/api/live/confirm", state.confirmation).then((reply) => {
      // The form moved on while the camera was being confirmed: its answer belongs to a question no longer shown.
      if (generation.current !== begun) return;
      if (reply.ok) reload();
      else setState(stateOf(reply));
    });
  }, [state, reload]);
  return { state, reload, confirm };
}
