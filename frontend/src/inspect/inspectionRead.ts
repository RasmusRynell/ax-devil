import { MAX_COPY_BYTES, MAX_INSPECTION_CHUNK_BYTES } from "../generated/api";
import type { ClientMessage, ServerMessage } from "../stream/protocol";
import { utf8Bytes } from "./utf8";

/** One current read; serial ownership and scheduling remain with Inspections. */
export type InspectionRead = {
  request: number;
  key: string;
  lane: number;
  token: string;
  field: number | "raw" | "pins" | null;
  path: boolean;
  offset: number;
};

/** Projects one bounded continuation request, or its terminal local refusal. */
export function readMessage(
  read: InspectionRead,
  pins: NonNullable<Extract<ClientMessage, { type: "InspectContinue" }>["pins"]>,
): ClientMessage | string {
  if (read.field === "pins" && utf8Bytes(JSON.stringify(pins), 60 * 1024) > 60 * 1024)
    return "Pinned fields exceed the inspection request budget";
  return {
    type: "InspectContinue",
    request: read.request,
    token: read.token,
    field: typeof read.field === "number" ? read.field : null,
    pins: read.field === "pins" ? pins : undefined,
    raw: read.field === "raw" ? true : undefined,
    path: read.path,
    offset: read.offset,
  };
}

/** Enforces the copy's byte ceiling and forward progress before appending anything. */
export function validChunk(
  copy: { received: number; total: number },
  result: Extract<
    Extract<ServerMessage, { type: "InspectContinued" }>["result"],
    { state: "Chunk" }
  >,
  bytes: number,
  raw: boolean,
): boolean {
  const received = copy.received + bytes;
  const next = received < result.total ? received : null;
  return (
    bytes <= MAX_INSPECTION_CHUNK_BYTES &&
    result.total <= MAX_COPY_BYTES &&
    received <= result.total &&
    ((raw && copy.received === 0) || result.total === copy.total) &&
    result.next === next &&
    (bytes > 0 || next === null)
  );
}
