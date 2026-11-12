// Generated from api_types::workspace by make; do not edit.

import type { BookmarkGroupInfo } from "./api_types.bookmarks";
import type { ContentInfo } from "./api_types.content";
import type { ErrorWire } from "./api_types.error";
import type { NewLiveWire } from "./api_types.live.new_live";
import type { WarningInfo } from "./api_types.playback.lane";

/**
 * The body of `POST /api/files/check`: the files a form is about to add, as typed.
 */
export type CheckFilesRequest = { 
/**
 * The paths; a relative one is relative to the backend's working folder.
 */
paths: Array<string>, };

/**
 * What the backend host makes of one path.
 */
export type FileCheck = { 
/**
 * The path as asked.
 */
path: string, 
/**
 * Why it cannot be read as a file; absent when it can.
 */
problem?: string, };

/**
 * One entry of a listed folder.
 */
export type FolderEntry = { 
/**
 * The file or folder name.
 */
name: string, 
/**
 * Whether it is a folder; otherwise a file.
 */
folder: boolean, };

/**
 * What `GET /api/files/folder` answers: one folder on the backend's machine, and where a listing can start.
 */
export type FolderListing = { 
/**
 * The folder's canonical path; absent when no folder was asked for.
 */
path?: string, 
/**
 * The folder that holds it; absent at a root and when no folder was asked for.
 */
parent?: string, 
/**
 * The visible entries it holds, folders first, each group by name ignoring case.
 */
entries: Array<FolderEntry>, 
/**
 * Where to start: the home folder, then the filesystem roots. Empty when a folder was asked for.
 */
places: Array<FolderPlace>, };

/**
 * A folder to start a listing from.
 */
export type FolderPlace = { 
/**
 * What to call it: `Home`, or the root's own path.
 */
name: string, 
/**
 * The folder's absolute path.
 */
path: string, };

/**
 * An entry or lane to hide or show.
 */
export type HiddenTarget = { 
/**
 * The entry, by its content id.
 */
entry: string, 
/**
 * The lane's label; the whole entry when absent.
 */
lane?: string, };

/**
 * What an item is, from its recipe.
 */
export type ItemKind = "video" | "playlist" | "live" | "unreadable";

/**
 * What an item was made from, as stored: a `$NAME` reference stays one and a literal password is left out. The name
 * is the one the user gave, absent for the default.
 */
export type ItemRecipe = { "type": "Video", 
/**
 * The file.
 */
path: string, 
/**
 * The user's name for the item.
 */
label: string | null, 
/**
 * The overlay files, each a lane.
 */
overlays: Array<NewOverlayWire>, } | { "type": "Playlist", 
/**
 * The resolver id.
 */
resolver: string, 
/**
 * The user's name for the item.
 */
label: string | null, 
/**
 * The settings the resolver's schema declares.
 */
settings: Record<string, unknown>, } | { "type": "Live" } & NewLiveWire;

/**
 * One workspace item and how far it has resolved.
 */
export type ItemState = { 
/**
 * Names the item.
 */
id: string, 
/**
 * The name the item shows: what the user named it, or its default.
 */
label: string, 
/**
 * What the item is, which a sidebar view of one kind lists before it has resolved.
 */
kind: ItemKind, 
/**
 * Whether a viewer should open for the item once it resolves. Stays set; the frontend remembers which
 * viewers it has opened.
 */
open: boolean, 
/**
 * Whether the item has Content yet.
 */
resolution: ResolutionState, 
/**
 * Pending source revisions: a playlist viewer offers only its shown entry, as resolver inputs do not change
 * the overlays on screen. Single-file viewers use the item revision.
 */
changes?: SourceChangesInfo, 
/**
 * Why the last reload of the open viewer failed, until the next. The viewer keeps its old Content.
 */
reload_failure?: ReloadFailure, 
/**
 * What the item was made from, for an audience with host details: what Edit starts from and Information lists.
 */
recipe?: ItemRecipe, 
/**
 * For a live stream, for an audience with host details: what its hosts read as under the Session's right to
 * expand `$NAME` references, the one rule resolving and labels follow.
 */
shown?: LiveShown, };

/**
 * The hosts of a live stream as the Session reads them: a `$NAME` reference is its value (empty when the variable
 * is not set) where the Session may expand references, and the text as typed where it may not.
 */
export type LiveShown = { 
/**
 * The camera host.
 */
host: string, 
/**
 * The MQTT broker host; `null` for another overlay.
 */
broker_host: string | null, };

/**
 * What the user asked to add; the intake turns each into an item.
 */
export type NewItem = { "type": "Video", 
/**
 * The file; a relative path is relative to the backend's working folder.
 */
path: string, 
/**
 * The item's name; the file name when absent or blank.
 */
label?: string, 
/**
 * The overlay files, each shown as its own lane.
 */
overlays: Array<NewOverlayWire>, } | { "type": "Live" } & NewLiveWire;

/**
 * An overlay file to add to a video.
 */
export type NewOverlayWire = { 
/**
 * The file.
 */
path: string, 
/**
 * The decoder handler id; the only decoder for the file's extension when absent.
 */
handler: string | null, 
/**
 * The lane's name; the file name when absent, numbered ` (2)`, ` (3)` when the video already shows the file.
 */
label: string | null, 
/**
 * The options the decoder's form declares; checked by the intake.
 */
options: Record<string, unknown>, };

/**
 * What `GET /api/files/exists` answers: whether anything, hidden or not, is at a path on the backend's machine.
 */
export type PathExists = { 
/**
 * Whether a file or folder is there.
 */
exists: boolean, };

/**
 * A workspace file opened or saved lately.
 */
export type RecentWorkspace = { 
/**
 * The file to open, absolute: a workspace file, or the recovery copy of unsaved changes to one.
 */
path: string, 
/**
 * The containing folder for display, with the home shortened only for an audience allowed host details.
 */
folder: string, 
/**
 * The workspace file name this row is for, with its suffix: the saved file's for a recovery copy of it.
 */
name: string, 
/**
 * When it was last used, in Unix milliseconds.
 */
usedMs: number, };

/**
 * A reload that failed.
 */
export type ReloadFailure = { 
/**
 * Which attempt failed, so a dismissed failure does not hide the next one.
 */
attempt: number, 
/**
 * Why.
 */
error: ErrorWire, };

/**
 * How far an item has resolved.
 */
export type ResolutionState = { "status": "Pending" } | { "status": "Resolved", 
/**
 * What the item resolved to.
 */
content: ContentInfo, } | { "status": "Failed" } & ErrorWire;

/**
 * One row of the workspace tree.
 */
export type RowInfo = { 
/**
 * Names the row for as long as what it stands for exists.
 */
key: string, 
/**
 * The item the row belongs to, which a viewer is opened on.
 */
item: string, 
/**
 * What the row stands for.
 */
kind: RowKindWire, 
/**
 * What the user reads.
 */
label: string, 
/**
 * What tells it from a sibling with the same label: where it comes from, or its number; empty for most rows. The
 * sidebar shows it under the label, and the search matches it.
 */
detail: string, 
/**
 * The file or camera it is read from, which the Offline view's Location order sorts by; none for a playlist, a row
 * not resolved, and an audience without host details.
 */
location: string | null, 
/**
 * Whether it can be opened.
 */
status: RowStatusInfo, 
/**
 * Whether the user hid it.
 */
hidden: boolean, 
/**
 * The entry that opening the row opens, by the id the playback socket takes.
 */
entry: string | null, 
/**
 * What resolving left out of an item's Content; empty for entries and lanes.
 */
warnings: Array<WarningInfo>, };

/**
 * What a row stands for.
 */
export type RowKindWire = "item" | "playlist" | "entry" | "lane";

/**
 * Whether what a row stands for can be opened.
 */
export type RowStatusInfo = { "status": "Pending" } | { "status": "Unavailable" } & ErrorWire | { "status": "Ready" };

/**
 * Pending changes contain only identities already carried by the Content projection.
 */
export type SourceChangesInfo = { 
/**
 * A pending whole-item change, including resolver inputs and files read only by a failed attempt.
 */
item: number | null, 
/**
 * Pending changes by active entry identity; no file paths cross this boundary.
 */
entries: { [key in string]?: number }, };

/**
 * The workspace and what is known about each item, as one audience may see it.
 */
export type WorkspaceState = { 
/**
 * The user's name for the workspace; `null` is Untitled.
 */
name: string | null, 
/**
 * Whether the workspace differs from what was last opened or saved.
 */
modified: boolean, 
/**
 * Whether changes restored from a recovery copy await Keep or Discard.
 */
recovered: boolean, 
/**
 * Counts replacements of the workspace; every viewer closes when it changes.
 */
epoch: number, 
/**
 * The workspace file on the host. Only for an audience with host details.
 */
file: string | null, 
/**
 * Every item in the order it was added.
 */
items: Array<ItemState>, 
/**
 * The sidebar tree flattened: each video or unresolved item, each playlist, their entries and lanes, in tree order. Lists only the items
 * `items` lists.
 */
rows: Array<RowInfo>, 
/**
 * The bookmarks and reviewed marks of the items `items` lists, a group for each video or entry with any.
 */
bookmarks: Array<BookmarkGroupInfo>, };
