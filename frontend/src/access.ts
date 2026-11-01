import { createContext, useContext } from "react";
import type { Grant } from "./generated/api";

/** What this page presents to its backend: the server password, or the join link of one session. */
export type Credential =
  | { kind: "password"; password: string }
  | { kind: "join"; session: string; secret: string };

const STORED = "ax-devil.credential";

function parse(fragment: string): Credential | null {
  const split = fragment.indexOf("=");
  if (split < 0) return null;
  let value: string;
  try {
    value = decodeURIComponent(fragment.slice(split + 1));
  } catch {
    return null;
  }
  const name = fragment.slice(0, split);
  if (name === "password" && value) return { kind: "password", password: value };
  const dot = value.indexOf(".");
  if (name === "join" && dot > 0 && dot < value.length - 1)
    return { kind: "join", session: value.slice(0, dot), secret: value.slice(dot + 1) };
  return null;
}

/**
 * Moves a credential from the URL fragment (`#join=<id>.<secret>` or `#password=<pw>`) into `sessionStorage` and
 * removes the fragment from the address bar, so it reaches no history entry, query string or log. Call it once
 * before the app renders.
 */
export function adoptFragment(): void {
  const { hash, pathname, search } = window.location;
  if (!hash) return;
  const found = parse(hash.slice(1));
  if (found) window.sessionStorage.setItem(STORED, JSON.stringify(found));
  window.history.replaceState(window.history.state, "", `${pathname}${search}`);
}

/** What a server password may be made of: printable ASCII without spaces, which an HTTP header carries as it is. */
export const PASSWORD_RULE = "Use printable ASCII characters without spaces.";

/** Whether a server password can travel in the `Authorization` header unchanged; the server accepts no other. */
export const sendable = (password: string): boolean => /^[\x21-\x7e]+$/.test(password);

/** Replaces the server password this tab holds. */
export function holdPassword(password: string): void {
  window.sessionStorage.setItem(STORED, JSON.stringify({ kind: "password", password }));
}

/** The credential this tab holds, if any. */
export function credential(): Credential | null {
  const stored = window.sessionStorage.getItem(STORED);
  return stored ? (JSON.parse(stored) as Credential) : null;
}

/** What the backend lets this page do, from `GET /api/info`; empty until the backend has answered. */
export const GrantsContext = createContext<readonly Grant[]>([]);

/** The grants of this page on its backend. */
export function useGrants(): readonly Grant[] {
  return useContext(GrantsContext);
}
