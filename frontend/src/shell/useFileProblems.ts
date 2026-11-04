import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { FileCheck } from "../generated/api";

/** How long the paths stay as typed before the backend is asked about them. */
export const FILE_CHECK_DELAY_MS = 300;

const NONE: ReadonlyMap<string, string> = new Map();

/**
 * What the backend host finds wrong with each of `paths` (blank ones left out by the caller), asked once typing
 * pauses. A change drops the question in flight, so an answer is never used for paths that have moved on. `checking`
 * holds from a change until its answer; a backend that cannot be asked leaves every path without a problem, and the
 * add judges the files itself.
 */
export function useFileProblems(paths: readonly string[]): {
  problems: ReadonlyMap<string, string>;
  checking: boolean;
} {
  const key = JSON.stringify(paths);
  const [answer, setAnswer] = useState<{ key: string; problems: ReadonlyMap<string, string> }>();
  useEffect(() => {
    const asked = JSON.parse(key) as string[];
    if (asked.length === 0) return;
    const request = new AbortController();
    const timer = setTimeout(() => {
      void apiFetch("/api/files/check", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ paths: asked }),
        signal: request.signal,
      })
        .then((response) => (response.ok ? (response.json() as Promise<FileCheck[]>) : []))
        .catch((): FileCheck[] => [])
        .then((checks) => {
          if (request.signal.aborted) return;
          const problems = checks.flatMap((check) =>
            check.problem ? [[check.path, check.problem] as const] : [],
          );
          setAnswer({ key, problems: new Map(problems) });
        });
    }, FILE_CHECK_DELAY_MS);
    return () => {
      clearTimeout(timer);
      request.abort();
    };
  }, [key]);
  const current = answer?.key === key;
  return {
    problems: current ? answer.problems : NONE,
    checking: paths.length > 0 && !current,
  };
}
