// Generated from sessions::session::authorize by make; do not edit.

/**
 * What a seat may do, one flag per kind of command a window offers a control for; sent to the seat so its UI disables
 * what the backend would refuse. Derived from the same role table as `authorize`.
 */
export type Can = { 
/**
 * Change what the seat's window shows.
 */
view: boolean, 
/**
 * Change the Doc's content.
 */
content: boolean, 
/**
 * Read the host's files.
 */
host: boolean, 
/**
 * Change who the session is shared with.
 */
control: boolean, };

/**
 * A stream a seat asks a ticket for: one entry of the Content at one commit of its resolution. Authority over the
 * stream ends with the generation, and the Content's kind decides what a seat may demand of it. A video's one entry
 * has the Content's own id.
 */
export type ContentRef = { 
/**
 * The Content's id. Stable across re-resolution.
 */
content: string, 
/**
 * The commit of the Content's resolution, as the Doc names it. Re-resolving or reopening gives a new one.
 */
generation: number, 
/**
 * The entry the stream plays; for a video or a live video, the Content's own id.
 */
entry: string, 
/**
 * The viewer the stream is for. A seat that follows streams what the presenter's viewer shows; any other seat's
 * stream does not depend on it.
 */
viewer: number, };

/**
 * Why a command or a connection was refused. A code and nothing else: the client words it.
 */
export type RefusalCode = "SeatGone" | "NotAllowed" | "NotSharing" | "AlreadySharing" | "ContentUnavailable" | "TooManyStreams" | "BadTicket" | "Link" | "Full" | "Invalid";
