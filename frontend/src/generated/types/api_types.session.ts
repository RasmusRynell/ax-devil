// Generated from api_types::session by make; do not edit.

import type { BookmarkCommand } from "./api_types.bookmarks";
import type { ErrorWire } from "./api_types.error";
import type { LanePolicyWire } from "./api_types.playback.lane";
import type { HiddenTarget, NewItem, WorkspaceState } from "./api_types.workspace";
import type { CursorOptions, EndedReason, SeatMode } from "./sessions";
import type { Access } from "./sessions.session";
import type { Can, ContentRef, RefusalCode } from "./sessions.session.authorize";
import type { ControlCommand } from "./sessions.session.commands";
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
 * A change to the Doc's content; the backend runs the intake on every item before it applies any.
 */
export type DocCommand = { "type": "addItems", 
/**
 * What to add, in order.
 */
items: Array<NewItem>, 
/**
 * Whether to publish the results together and open a viewer for the first that resolved.
 */
open: boolean, } | { "type": "addResolved", 
/**
 * The item the resolve answered with.
 */
item: string, 
/**
 * The row's label; the resolver's name when absent or blank.
 */
label: string | null, } | { "type": "openViewer", 
/**
 * The item.
 */
item: string, } | { "type": "closeViewer", 
/**
 * The item.
 */
item: string, } | { "type": "resolve", 
/**
 * The item.
 */
item: string, } | { "type": "remove", 
/**
 * The item.
 */
item: string, } | { "type": "renameItem", 
/**
 * The item.
 */
item: string, 
/**
 * The name the user entered.
 */
label: string, } | { "type": "editItem", 
/**
 * The item.
 */
item: string, 
/**
 * What the item becomes; the intake judges it as it judges an item to add.
 */
new: NewItem, } | { "type": "editResolved", 
/**
 * The item to edit.
 */
item: string, 
/**
 * The item the resolve answered with.
 */
ticket: string, 
/**
 * The item's name; the resolver's when absent or blank.
 */
label: string | null, } | { "type": "setHidden", 
/**
 * What to hide or show.
 */
target: HiddenTarget, 
/**
 * Whether it is hidden afterwards.
 */
hidden: boolean, } | { "type": "setLaneMatching", 
/**
 * The item.
 */
item: string, 
/**
 * The lane's slot, which names it in every entry.
 */
slot: string, 
/**
 * How the lane matches.
 */
policy: LanePolicyWire, } | { "type": "renameWorkspace", 
/**
 * The name the user entered.
 */
name: string, 
/**
 * The `epoch` of the workspace the name was entered for.
 */
epoch: number, } | { "type": "bookmark", 
/**
 * The change.
 */
command: BookmarkCommand, };

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
 * What a seat asks for, settled by its own `Ack`. Changes to a seat's view travel the View lane instead.
 */
export type SessionCommand = { "type": "content", 
/**
 * The change.
 */
command: DocCommand, } | { "type": "follow" } | { "type": "control", 
/**
 * The change.
 */
control: ControlCommand, } | { "type": "requestTicket", 
/**
 * The stream.
 */
key: ContentRef, } | { "type": "workspace", 
/**
 * The change.
 */
command: WorkspaceCommand, };

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
serverUs: number, } | { "type": "cursor", 
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
 * What a seat asks of the workspace file. A path is absolute, on the host.
 */
export type WorkspaceCommand = { "type": "new" } | { "type": "open", 
/**
 * The file.
 */
path: string, } | { "type": "save", 
/**
 * The file to save as; the workspace's own file when absent.
 */
path?: string, 
/**
 * Whether Save As may replace a file that is already there.
 */
replace?: boolean, } | { "type": "keep" } | { "type": "discard" } | { "type": "exportBookmarks", 
/**
 * The file, absolute.
 */
path: string, };
