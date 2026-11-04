import { apiFetch } from "../api";
import { reasonOf } from "../errors";
import type { ErrorWire } from "../generated/api";

/** Why the backend refused a request: its HTTP status (0 when it never answered) and a reason to show. */
export type Refused = { status: number; reason: string };

/** Posts `body` as JSON to a route that answers with no content; resolves to why it was refused, else `null`. */
export async function post(url: string, body: unknown = {}): Promise<Refused | null> {
  try {
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.ok) return null;
    const error = (await response.json().catch(() => null)) as ErrorWire | null;
    return {
      status: response.status,
      reason: error?.code ? reasonOf(error) : `the backend answered ${response.status}`,
    };
  } catch (error) {
    return { status: 0, reason: error instanceof Error ? error.message : String(error) };
  }
}

/** Like `post`, resolving to the reason alone. */
export const send = (url: string, body: unknown = {}): Promise<string | null> =>
  post(url, body).then((refused) => refused?.reason ?? null);
