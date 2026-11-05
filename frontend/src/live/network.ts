/** The cameras the network scan finds: the stream of snapshots, how they are ordered and worded. */

import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { NetworkRow, NetworkSignIn, NetworkSnapshot, ScanStarted } from "../generated/api";
import type { Tone } from "../kit/look";
import { ask, send } from "./send";

const RETRY_MS = 2000;

/** The orders the Live view's rows can be listed in; by what a scan learned of each camera, those missing it last. */
export const ORDERS = [
  ["address", "Address"],
  ["name", "Name"],
  ["model", "Model"],
  ["firmware", "Firmware"],
  ["uptime", "Uptime"],
] as const;
export type Order = (typeof ORDERS)[number][0];

/** The numbers in a text, so `11.10` follows `11.9`. */
const numbers = (text: string): number[] => (text.match(/\d+/g) ?? []).map(Number);

/** Compares number lists as versions: a shorter list that starts the same comes first. */
function compareNumbers(a: readonly number[], b: readonly number[]): number {
  for (let at = 0; at < Math.max(a.length, b.length); at++) {
    const difference = (a[at] ?? -1) - (b[at] ?? -1);
    if (difference !== 0) return difference;
  }
  return 0;
}

const unknown: Record<Exclude<Order, "name">, (row: NetworkRow) => boolean> = {
  address: () => false,
  model: (row) => row.identity.model === "",
  firmware: (row) => row.identity.firmware === "",
  uptime: (row) => row.uptime_s === null,
};

const compare: Record<Exclude<Order, "name">, (a: NetworkRow, b: NetworkRow) => number> = {
  address: () => 0,
  model: (a, b) =>
    a.identity.model.localeCompare(b.identity.model, undefined, { sensitivity: "base" }),
  firmware: (a, b) => compareNumbers(numbers(a.identity.firmware), numbers(b.identity.firmware)),
  uptime: (a, b) => (a.uptime_s ?? 0) - (b.uptime_s ?? 0),
};

/**
 * Compares two cameras' scan results in a device order: a camera without one comes last, one missing the value
 * after those with it, and ties go by address. Equal for the name order, which the row's label decides.
 */
export function compareScans(order: Order, a: NetworkRow | null, b: NetworkRow | null): number {
  if (order === "name") return 0;
  if (!a || !b) return Number(!a) - Number(!b);
  return (
    Number(unknown[order](a)) - Number(unknown[order](b)) ||
    compare[order](a, b) ||
    compareNumbers(numbers(a.address), numbers(b.address))
  );
}

/** How a sign-in outcome is worded and toned. */
export const SIGN_IN: Record<NetworkSignIn["state"], { label: string; tone: Tone }> = {
  SignedIn: { label: "Signed in", tone: "success" },
  Refused: { label: "Credentials refused", tone: "danger" },
  Failed: { label: "Sign-in failed", tone: "caution" },
  NotTried: { label: "Not tried", tone: "neutral" },
};

/** How long a camera has been up, such as `3d 4h`, `2h 5m` or `0m`. */
export function uptimeText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days >= 1) return `${days}d ${hours % 24}h`;
  if (hours >= 1) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

/** Complete lines of `pending` followed by `chunk`, and the unfinished rest. */
export function splitLines(pending: string, chunk: string): { lines: string[]; rest: string } {
  const parts = (pending + chunk).split("\n");
  const rest = parts.pop() ?? "";
  return { lines: parts.filter((line) => line.trim() !== ""), rest };
}

/**
 * The scan as the backend last said; `dropped` while the stream is down and being reopened. `epoch` counts the times
 * the stream has been opened, for what must not outlive a connection: a restarted backend numbers its scans again.
 * `connected` is whether the stream has opened and not dropped since, so what is shown can be taken as current.
 */
export type Network = {
  snapshot: NetworkSnapshot | null;
  dropped: boolean;
  epoch: number;
  connected: boolean;
};

/**
 * Follows `GET /api/network` while `enabled`, reopening the stream after a drop. A snapshot of an older scan than one
 * already shown is ignored, within one connection.
 */
export function useNetwork(enabled: boolean): Network {
  const [state, setState] = useState<Omit<Network, "connected">>({
    snapshot: null,
    dropped: false,
    epoch: 0,
  });
  useEffect(() => {
    if (!enabled) return;
    const stop = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const follow = async () => {
      let latest = -1;
      try {
        const response = await apiFetch("/api/network", { signal: stop.signal });
        if (!response.ok || !response.body) throw new Error(`answered ${response.status}`);
        setState((now) => ({ ...now, dropped: false, epoch: now.epoch + 1 }));
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          const { lines, rest } = splitLines(pending, decoder.decode(value, { stream: true }));
          pending = rest;
          for (const line of lines) {
            const snapshot = JSON.parse(line) as NetworkSnapshot;
            if (snapshot.scan < latest) continue;
            latest = snapshot.scan;
            setState((now) => ({ ...now, snapshot, dropped: false }));
          }
        }
      } catch {
        // Aborted, or the connection dropped: reopened below.
      }
      if (stop.signal.aborted) return;
      setState((now) => ({ ...now, dropped: true }));
      timer = setTimeout(() => void follow(), RETRY_MS);
    };
    void follow();
    return () => {
      stop.abort();
      clearTimeout(timer);
    };
  }, [enabled]);
  return enabled
    ? { ...state, connected: state.epoch > 0 && !state.dropped }
    : { snapshot: null, dropped: false, epoch: 0, connected: false };
}

/** What a refused scan shows in the header: a range the backend cannot scan, or a scan that could not start. */
export type ScanProblem = { text: "Invalid IP range" | "Scan failed"; reason: string };

/**
 * Starts a scan of `range`; `undefined` scans the `scan.range` setting, blank the local network. `signIn` says for this
 * scan whether to try the logins on the cameras found; `undefined` follows the `scan.sign_in` setting. Resolves to
 * the started scan's number, which its snapshots carry, else to why it was refused.
 */
export async function rescan(range?: string, signIn?: boolean): Promise<number | ScanProblem> {
  const answer = await ask("/api/network/scan", { range, sign_in: signIn });
  if (!(answer instanceof Response))
    return {
      text: answer.status === 422 ? "Invalid IP range" : "Scan failed",
      reason: answer.reason,
    };
  try {
    return ((await answer.json()) as ScanStarted).scan;
  } catch {
    return { text: "Scan failed", reason: "the backend's answer could not be read" };
  }
}

/** Stops the running scan, keeping what it found. */
export const cancelScan = (): Promise<string | null> => send("/api/network/cancel");
