import { useEffect, useState } from "react";
import { ask } from "../client/ask";
import type { FileCheck } from "../generated/api";

/** How long the paths stay as typed before the backend is asked about them. */
export const FILE_CHECK_DELAY_MS = 300;

const NONE: ReadonlyMap<string, string> = new Map();

/**
 * What the backend host finds wrong with each of `paths` now, by path; a path without an entry is readable. A backend
 * that cannot be asked, or an aborted `signal`, leaves every path without a problem: the add judges the files itself.
 */
export async function checkFiles(
  paths: readonly string[],
  signal?: AbortSignal,
): Promise<ReadonlyMap<string, string>> {
  const answer = await ask<FileCheck[]>("/api/files/check", { json: { paths }, signal }).catch(
    () => null,
  );
  const checks = answer?.ok ? answer.value : [];
  return new Map(checks.flatMap((check) => (check.problem ? [[check.path, check.problem]] : [])));
}

/**
 * What the backend host finds wrong with each of `paths` (blank ones left out by the caller), asked once typing
 * pauses. A change drops the question in flight and the last answer, so an answer is never used for paths that have
 * moved on, nor for ones that moved back. `checking` holds from a change until its answer; a backend that cannot be asked leaves every path without a problem, and the
 * add judges the files itself.
 */
export function useFileProblems(paths: readonly string[]): {
  problems: ReadonlyMap<string, string>;
  checking: boolean;
} {
  const key = JSON.stringify(paths);
  const [answer, setAnswer] = useState<{ key: string; problems: ReadonlyMap<string, string> }>();
  const [asked, setAsked] = useState(key);
  if (asked !== key) {
    // Any edit forgets the last answer, even for paths that come back: the files may have changed meanwhile.
    setAsked(key);
    setAnswer(undefined);
  }
  useEffect(() => {
    const asked = JSON.parse(key) as string[];
    if (asked.length === 0) return;
    const request = new AbortController();
    const timer = setTimeout(() => {
      void checkFiles(asked, request.signal).then((problems) => {
        if (!request.signal.aborted) setAnswer({ key, problems });
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
