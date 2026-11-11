import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiFetch } from "./api";
import type { ApiError, Topic } from "./generated/api";

/** How long a failed read waits before it is tried again, one entry per retry. */
const RETRY_MS = [1000, 2000, 4000];

/** The revision of each topic the backend last announced on the Session connection. */
class Revisions {
  #held = new Map<Topic, number>();
  #listeners = new Set<() => void>();
  #notices = 0;
  #backends = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  /** What the topics were last announced as, in one text; `undefined` while none has been. */
  key = (topics: readonly Topic[]): string | undefined => {
    const held = topics.map((topic) => this.#held.get(topic));
    return held.every((revision) => revision === undefined) ? undefined : held.join(",");
  };

  /** How many notices came, repeats included: a repeated revision is how a reconnect asks a failed read to retry. */
  notices = (): number => this.#notices;

  /** Changes when a new connection starts, across which revisions are not ordered (a backend may have restarted). */
  backends = (): number => this.#backends;

  set = (topic: Topic, revision: number, fresh: boolean): void => {
    if (fresh) this.#backends += 1;
    this.#held.set(topic, revision);
    this.#notices += 1;
    this.#listeners.forEach((listener) => listener());
  };
}

const RevisionsContext = createContext<Revisions | null>(null);

/** Holds what the backend announced as changed, for the `useResource` hooks below it and the Session connection that reports it. */
export function ChangesProvider({ children }: { children: ReactNode }) {
  const [revisions] = useState(() => new Revisions());
  return <RevisionsContext value={revisions}>{children}</RevisionsContext>;
}

/**
 * Where the Session connection reports each `Changed` notice, and whether it is the first of its connection; `null`
 * outside a `ChangesProvider`.
 */
export function useNoticeSink(): ((topic: Topic, revision: number, fresh: boolean) => void) | null {
  return useContext(RevisionsContext)?.set ?? null;
}

const never = () => () => {};

/** Why a request failed: the backend's refusal, else the error's own message. */
export const refusalOf = (error: unknown): ApiError =>
  typeof error === "object" && error !== null && "reason" in error
    ? (error as ApiError)
    : { reason: error instanceof Error ? error.message : String(error), location: "" };

/** Asks the backend and reads the JSON answer; a failure throws the backend's refusal, or one naming the status. */
export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw error ?? { reason: `the backend answered ${response.status}`, location: "" };
  }
  return (await response.json()) as T;
}

const readJson = <T,>(path: string, signal: AbortSignal) => fetchJson<T>(path, { signal });

interface Options<T> {
  /** Reads the answer at `path`; the default is `fetchJson`. */
  read?: (path: string, signal: AbortSignal) => Promise<T>;
  /**
   * The revision a value already reflects, for an answer that carries its own (of one topic): a notice of that
   * revision asks for nothing, and `apply` ignores a value with a lower one.
   */
  revisionOf?: (value: T) => number;
}

/** What `useResource` gives. */
export interface Resource<T> {
  /** The last answer; `undefined` until there is one, and while `path` is `null`. */
  value: T | undefined;
  /** Why the last read failed, until one succeeds; the last value stays. */
  error: unknown;
  /** Shows a value the caller got elsewhere (a save's answer) instead of any read still pending, which it drops. */
  apply: (value: T) => void;
  /** Reads again now. */
  reload: () => void;
}

interface Held {
  path: string;
  /** What the topics were announced as when the read started, and the revision the answer itself carries. */
  started: string | undefined;
  own: number | undefined;
  /** The connection the answer was read or applied under. */
  backend: number;
}

const reflects = (held: Held | null, path: string, noticed: string | undefined): boolean =>
  held?.path === path && (held.started === noticed || String(held.own) === noticed);

/**
 * The answer of `path`, read when the component mounts or `path` changes and again whenever the backend announces
 * another revision of any of `topics` (also after a reconnect, and a restarted backend counts as one), so a topic
 * is named for every input of the answer. A notice that comes while a read runs lets it finish and asks once more.
 * A failed read is tried again after 1, 2 and 4 seconds, and by the next notice, repeated or not. `path` is `null`
 * while the answer is not wanted.
 */
export function useResource<T>(
  topics: Topic | readonly Topic[],
  path: string | null,
  given: Options<T> = {},
): Resource<T> {
  const revisions = useContext(RevisionsContext);
  const names = typeof topics === "string" ? [topics] : topics;
  const noticed = useSyncExternalStore(revisions?.subscribe ?? never, () => revisions?.key(names));
  const notices = useSyncExternalStore(revisions?.subscribe ?? never, () => revisions?.notices());
  const [shown, setShown] = useState<{ path: string | null; value?: T; error?: unknown }>({
    path: null,
  });
  const held = useRef<Held | null>(null);
  const running = useRef<{ path: string; request: AbortController } | null>(null);
  const retry = useRef<{ timer?: ReturnType<typeof setTimeout>; attempt: number }>({ attempt: 0 });
  const newest = useRef(noticed);
  const options = useRef(given);
  const [reloads, setReloads] = useState(0);
  useEffect(() => {
    newest.current = noticed;
    options.current = given;
  });

  const stop = useCallback(() => {
    clearTimeout(retry.current.timer);
    running.current?.request.abort();
    running.current = null;
  }, []);

  const begin = useCallback(
    (path: string) => {
      stop();
      const request = new AbortController();
      running.current = { path, request };
      const started = newest.current;
      const { read = readJson<T>, revisionOf } = options.current;
      read(path, request.signal).then(
        (value) => {
          if (request.signal.aborted) return;
          running.current = null;
          const own = revisionOf?.(value);
          held.current = { path, started, own, backend: revisions?.backends() ?? 0 };
          retry.current.attempt = 0;
          setShown({ path, value });
          // Notices that came during the read: one more for the newest.
          if (newest.current !== started) begin(path);
        },
        (error: unknown) => {
          if (request.signal.aborted) return;
          running.current = null;
          setShown((now) => ({ path, value: now.path === path ? now.value : undefined, error }));
          const wait = RETRY_MS[retry.current.attempt++];
          if (wait !== undefined) retry.current.timer = setTimeout(() => begin(path), wait);
        },
      );
    },
    [revisions, stop],
  );

  useEffect(() => {
    if (path === null) {
      stop();
      held.current = null;
      return;
    }
    if (reflects(held.current, path, noticed) || running.current?.path === path) return;
    retry.current.attempt = 0;
    begin(path);
  }, [path, noticed, notices, reloads, begin, stop]);
  useEffect(() => stop, [stop]);

  const apply = useCallback(
    (value: T) => {
      if (path === null) return;
      const own = options.current.revisionOf?.(value);
      const backend = revisions?.backends() ?? 0;
      const now = held.current;
      // A save's answer that comes after a newer read is older than what is shown; revisions only order within a connection.
      if (
        own !== undefined &&
        now?.path === path &&
        now.backend === backend &&
        own < (now.own ?? 0)
      )
        return;
      stop();
      // An answer with a revision of its own is compared with what was announced; any other stands for the newest.
      held.current = {
        path,
        started: own === undefined ? newest.current : undefined,
        own,
        backend,
      };
      setShown({ path, value });
      // A notice newer than the answer came while a read it replaced was running.
      if (own !== undefined && Number(newest.current) > own) begin(path);
    },
    [path, revisions, begin, stop],
  );
  const reload = useCallback(() => {
    held.current = null;
    stop();
    setReloads((count) => count + 1);
  }, [stop]);
  const current = shown.path === path ? shown : null;
  return { value: current?.value, error: current?.error, apply, reload };
}
