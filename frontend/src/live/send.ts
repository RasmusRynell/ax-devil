import { apiFetch } from "../api";
import { reasonOf } from "../errors";
import type { ErrorWire } from "../generated/api";

/** Why the backend refused a request: its HTTP status (0 when it never answered) and a reason to show. */
export type Refused = { status: number; reason: string };

/** Posts `body` as JSON: the response when the backend accepted it, else why it did not. */
export async function ask(url: string, body: unknown = {}): Promise<Response | Refused> {
  try {
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.ok) return response;
    const error = (await response.json().catch(() => null)) as ErrorWire | null;
    return {
      status: response.status,
      reason: error?.code ? reasonOf(error) : `the backend answered ${response.status}`,
    };
  } catch (error) {
    return { status: 0, reason: error instanceof Error ? error.message : String(error) };
  }
}

/** Posts `body` as JSON to a route whose answer is not read; resolves to why it was refused, else `null`. */
export async function post(url: string, body: unknown = {}): Promise<Refused | null> {
  const answer = await ask(url, body);
  return answer instanceof Response ? null : answer;
}

/** Like `post`, resolving to the reason alone. */
export const send = (url: string, body: unknown = {}): Promise<string | null> =>
  post(url, body).then((refused) => refused?.reason ?? null);
