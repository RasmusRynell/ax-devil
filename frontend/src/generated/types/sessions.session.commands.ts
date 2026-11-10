// Generated from sessions::session::commands by make; do not edit.

import type { Access } from "./sessions.session";

/**
 * The owner's control of sharing.
 */
export type ControlCommand = { "type": "share", 
/**
 * What every watcher may do.
 */
access: Access, } | { "type": "setAccess", 
/**
 * What every watcher may do.
 */
access: Access, } | { "type": "regenerateLink" } | { "type": "stop" };
