// Generated from sessions::session::commands by make; do not edit.

import type { Access } from "./sessions.session";

/**
 * An Owner seat's control of sharing.
 */
export type ControlCommand = { "type": "share", 
/**
 * What every Guest may do.
 */
access: Access, } | { "type": "setAccess", 
/**
 * What every Guest may do.
 */
access: Access, } | { "type": "regenerateLink" } | { "type": "stop" };
