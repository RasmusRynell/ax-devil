/**
 * What the `$NAME` references of the Add Live Stream form's defaults show as placeholders. The backend answers for the
 * references its binding rule lets expand for the host in the form, and never with a password; the form falls back to the
 * reference itself for the rest.
 */

import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { ExpansionsRequest, LiveExpansions } from "../generated/api";

/** How long the hosts stay as typed before they are asked about. */
const DEBOUNCE_MS = 200;

/** The dots standing for a password that is set; its length is not told. */
export const SET_PASSWORD = "••••••••";

/** The `$NAME` a default text is, else `null`. */
export const referenceIn = (text: string): string | null =>
  /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(text) ? text : null;

/**
 * Placeholder text for a default as Python shows it: the value, then the reference it comes from (`172.20.1.1  (via
 * $AX_DEVIL_TARGET_ADDR)`). `value` is what the reference expands to (`null` where the backend did not say, an empty
 * text where the variable is not set); a text that is no reference shows as it is, and a default that is nothing shows
 * nothing.
 */
export function viaText(
  raw: string,
  value: string | null | undefined,
  literal: string = raw,
): string | undefined {
  if (!raw) return undefined;
  if (referenceIn(raw) === null) return literal || undefined;
  if (value === null || value === undefined) return raw;
  return value ? `${value}  (via ${raw})` : undefined;
}

/**
 * What the references of the defaults show, for the hosts typed in the form, or `null` until the backend has said or
 * when `enabled` is false (only the person at this machine may ask).
 */
export function useExpansions(
  host: string,
  brokerHost: string,
  enabled: boolean,
): LiveExpansions | null {
  const [shown, setShown] = useState<LiveExpansions | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const request = new AbortController();
    const body: ExpansionsRequest = {
      host: host.trim() || undefined,
      broker_host: brokerHost.trim() || undefined,
    };
    const wait = setTimeout(() => {
      apiFetch("/api/live/expansions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: request.signal,
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((answer: LiveExpansions | null) => setShown(answer))
        .catch(() => undefined);
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(wait);
      request.abort();
    };
  }, [host, brokerHost, enabled]);
  return enabled ? shown : null;
}

const NO_VALUES: ReadonlyMap<string, string> = new Map();

/**
 * What the `$NAME` hosts of the workspace's streams (`references`) expand to, by reference, once the backend has said,
 * which the Live view groups and matches cameras by. A reference whose variable is not set has no entry. Nothing is
 * asked when `enabled` is false (only the person at this machine may ask).
 */
export function useExpandedHosts(
  references: readonly string[],
  enabled: boolean,
): ReadonlyMap<string, string> {
  const [values, setValues] = useState<ReadonlyMap<string, string>>(NO_VALUES);
  const wanted = references.join("\n");
  useEffect(() => {
    if (!enabled || wanted === "") return;
    const request = new AbortController();
    void Promise.all(
      wanted.split("\n").map((host) =>
        apiFetch("/api/live/expansions", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ host } satisfies ExpansionsRequest),
          signal: request.signal,
        })
          .then((response) => (response.ok ? response.json() : null))
          .then((answer: LiveExpansions | null) => [host, answer?.host ?? ""] as const)
          .catch(() => [host, ""] as const),
      ),
    ).then((pairs) => {
      if (!request.signal.aborted) setValues(new Map(pairs.filter(([, value]) => value)));
    });
    return () => request.abort();
  }, [wanted, enabled]);
  return enabled && wanted !== "" ? values : NO_VALUES;
}
