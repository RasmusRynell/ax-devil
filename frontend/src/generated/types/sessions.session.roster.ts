// Generated from sessions::session::roster by make; do not edit.

import type { SeatMode, SeatRole } from "./sessions";

/**
 * One seat of a Session, as every seat sees it.
 */
export type SeatInfo = { 
/**
 * Names the seat in `Roster` and `Cursor` messages.
 */
id: number, 
/**
 * `Guest N`.
 */
name: string, 
/**
 * An index into the UI's palette of participant colors.
 */
color: number, 
/**
 * Owner or Guest.
 */
role: SeatRole, 
/**
 * Following or exploring.
 */
mode: SeatMode, 
/**
 * False while the seat is within its grace period.
 */
present: boolean, 
/**
 * Whether the seat broadcasts its cursor.
 */
sharesCursor: boolean, };
