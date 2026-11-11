// Generated from api_types::session by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { SessionCommand } from "./api_types.session.commands";
import type { WorkspaceState } from "./api_types.workspace";
import type { CursorOptions, EndedReason, SeatMode, SeatRole } from "./sessions";
import type { Access } from "./sessions.session";
import type { Can, RefusalCode } from "./sessions.session.authorize";
import type { SeatInfo } from "./sessions.session.roster";
import type { View } from "./sessions.session.views";

/**
 * How a command ended.
 */
export type AckOutcome = { "status": "ok" } | { "status": "refused", 
/**
 * Why.
 */
code: RefusalCode, } | { "status": "failed", 
/**
 * Why.
 */
error: ErrorWire, };

/**
 * A position inside one element, which receivers find by its id.
 */
export type CursorElement = { 
/**
 * The element's `data-cursor-id`, at most [`CURSOR_NAME_MAX`] characters.
 */
id: string, 
/**
 * Left to right, a fraction of the element's width.
 */
x: number, 
/**
 * Top to bottom, a fraction of the element's height.
 */
y: number, };

/**
 * A cursor position. Every `x` and `y` is a fraction in `[0, 1]`, clamped by the backend, so windows of any size
 * agree.
 */
export type CursorPoint = { 
/**
 * What `x` and `y` are measured in.
 */
region: CursorRegion, 
/**
 * What the region shows right now: the sidebar view id, or the open viewer's id or `welcome`.
 */
scope: string, 
/**
 * Left to right, a fraction of the region's width.
 */
x: number, 
/**
 * Top to bottom, a fraction of the region's height.
 */
y: number, 
/**
 * The element under the pointer, when it has an id.
 */
element?: CursorElement, };

/**
 * The part of the window a cursor position belongs to.
 */
export type CursorRegion = "titleBar" | "activityBar" | "sidebar" | "main";

/**
 * The Doc as a Guest or an Owner may see it. Built only by the backend's projection.
 */
export type DocWire = { 
/**
 * The items, how they resolved, and which have a viewer open.
 */
workspace: WorkspaceState, };

/**
 * What this seat is allowed to do, in the rows of the role table.
 */
export type SeatAccess = "owner" | "watch" | "explore";

/**
 * How a client asks to be admitted; part of the first message of every socket.
 */
export type SessionAuth = { "type": "own", 
/**
 * The server password; only a server that has one looks at it.
 */
serverPassword?: string, } | { "type": "attach", 
/**
 * The Session's id.
 */
session: string, 
/**
 * The server password; only a server that has one looks at it.
 */
serverPassword?: string, } | { "type": "join", 
/**
 * The link's `<id>.<secret>`.
 */
link: string, };

/**
 * What a client sends over the Session socket. Anything a seat's standing does not allow is answered with a refused
 * `Ack` (commands) or settled without effect (views); only a malformed or oversized message, or more commands than
 * the command budget, closes the socket.
 */
export type SessionClientMessage = { "type": "hello", 
/**
 * How to be admitted. Not looked at when `resume` is set: the token alone is the credential.
 */
auth: SessionAuth, 
/**
 * The token from the last `Welcome`: takes that seat back instead of creating or joining.
 */
resume?: string, 
/**
 * This seat's cursor choices.
 */
cursors: CursorOptions, } | { "type": "view", 
/**
 * Counts this client's views; `views_done` reports the highest one finished.
 */
seq: number, 
/**
 * The view.
 */
view: View, } | { "type": "command", 
/**
 * Counts this client's commands; the `Ack` echoes it.
 */
seq: number, 
/**
 * The change.
 */
cmd: SessionCommand, } | { "type": "cursor", 
/**
 * The new position.
 */
at: CursorPoint | null, } | { "type": "cursorOptions" } & CursorOptions | { "type": "timeSync", 
/**
 * Names the request.
 */
id: number, 
/**
 * The client's clock when it sent this.
 */
clientUs: number, } | { "type": "leave" };

/**
 * What the backend sends over the Session socket.
 */
export type SessionServerMessage = { "type": "welcome", 
/**
 * The Session's id, which `GET /api/sessions` lists it by while it is shared.
 */
session: string, 
/**
 * This seat's id.
 */
seat: number, 
/**
 * Takes this seat back after a disconnect; each welcome brings a new one and kills the old.
 */
resume: string, 
/**
 * The highest View `seq` of this seat that is finished; send later views above it.
 */
viewsDone: number, 
/**
 * The backend's clock when it sent this.
 */
serverUs: number, } | { "type": "doc", 
/**
 * Counts changes to the Doc.
 */
docRev: number, 
/**
 * The Doc as this seat may see it.
 */
doc: DocWire, } | { "type": "seat", 
/**
 * Counts changes any seat can see.
 */
rev: number, 
/**
 * The Doc revision `view` belongs to; the Doc for it has been sent.
 */
docRev: number, 
/**
 * The presenter's view revision.
 */
presenterRev: number, 
/**
 * The seat whose change made the presenter's view as it is; absent when the server did.
 */
presenterBy?: number, 
/**
 * What this seat may do.
 */
access: SeatAccess, 
/**
 * What this seat may do, from the same role table the backend enforces.
 */
can: Can, 
/**
 * Whether this seat follows or explores.
 */
mode: SeatMode, 
/**
 * Sharing, while it is on.
 */
sharing?: SharingInfo, 
/**
 * What this seat shows: its own view while exploring, the presenter's otherwise.
 */
view: View, 
/**
 * The highest View `seq` of this seat that is finished: accepted, refused or overwritten.
 */
viewsDone: number, } | { "type": "roster", 
/**
 * The seats.
 */
participants: Array<SeatInfo>, } | { "type": "ack", 
/**
 * The command's `seq`.
 */
seq: number, 
/**
 * How it ended.
 */
outcome: AckOutcome, } | { "type": "ticket", 
/**
 * The `RequestTicket` command's `seq`.
 */
seq: number, 
/**
 * The bearer ticket: never put it in a URL or a log.
 */
ticket: string, 
/**
 * How long it can be redeemed, in milliseconds.
 */
expiresMs: number, } | { "type": "timeSyncReply", 
/**
 * The request's `id`.
 */
id: number, 
/**
 * The request's `client_us`.
 */
clientUs: number, 
/**
 * The backend's clock when it answered.
 */
serverUs: number, } | { "type": "heartbeat", 
/**
 * The backend's clock when it sent this.
 */
serverUs: number, } | { "type": "changed", 
/**
 * What changed.
 */
topic: Topic, 
/**
 * Differs from the last one sent for the topic whenever its answer may differ, also across a backend restart.
 */
revision: number, } | { "type": "cursor", 
/**
 * Whose cursor.
 */
participant: number, 
/**
 * Where it is now, or `null` when it left the window.
 */
at: CursorPoint | null, } | { "type": "ended", 
/**
 * Why.
 */
reason: EndedReason, };

/**
 * A line of `GET /api/sessions`: a Session of this backend.
 */
export type SessionSummary = { 
/**
 * What to attach with.
 */
id: string, 
/**
 * Whether an Owner seat is connected.
 */
ownerPresent: boolean, 
/**
 * Seats in the Session, away ones included.
 */
participants: number, 
/**
 * The seat attaching would give the caller now; a Session that would give none is not listed.
 */
seat: SeatRole, 
/**
 * The join link URL; absent when this backend cannot be reached by other machines.
 */
link?: string, 
/**
 * Why there is no link, when other machines cannot reach this backend.
 */
linkProblem?: string, };

/**
 * Sharing, while it is on.
 */
export type SharingInfo = { 
/**
 * What every Guest may do.
 */
access: Access, 
/**
 * The join link URL; an Owner seat only.
 */
link?: string, 
/**
 * Why there is no link, when other machines cannot reach this backend; an Owner seat only.
 */
linkProblem?: string, };

/**
 * What a `Changed` notice names: the answers a client asks for again. Every topic's routes are the Owner's, so only
 * Owner connections are told.
 */
export type Topic = "settings" | "sessions" | "recents" | "plugins";
