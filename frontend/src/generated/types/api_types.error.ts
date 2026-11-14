// Generated from api_types::error by make; do not edit.

import type { LiveReason } from "./api_types.live";
import type { ErrorCode } from "./foundation.error";

/**
 * The values a client may fill into the message for a code. Only ids, never text.
 */
export type ErrorParams = { 
/**
 * The workspace item the failure is about.
 */
item?: string, 
/**
 * Why a camera did not answer, for `CameraFailed`; the client words it.
 */
camera?: LiveReason, };

/**
 * A failure as the wire carries it: the code and its parameters for everyone, and for an audience with host
 * details also the reason and the location.
 */
export type ErrorWire = { 
/**
 * The public kind.
 */
code: ErrorCode, 
/**
 * The values the code's message needs.
 */
params: ErrorParams, 
/**
 * What went wrong, in words for the user. Only for an audience with host details.
 */
reason?: string, 
/**
 * What it is about, such as the file path or the settings field key. Only for an audience with host details,
 * and only when it is about something.
 */
location?: string, };
