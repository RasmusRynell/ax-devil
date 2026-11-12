// Generated from api_types::session::commands by make; do not edit.

import type { BookmarkCommand } from "./api_types.bookmarks";
import type { LanePolicyWire } from "./api_types.playback.lane";
import type { HiddenTarget, NewItem } from "./api_types.workspace";
import type { ContentRef } from "./sessions.session.authorize";
import type { ControlCommand } from "./sessions.session.commands";
import type { Overlay, Viewport } from "./sessions.session.views";

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
 * What a seat asks for, settled by its own `Ack`. Changes to a seat's view travel the View lane instead.
 */
export type SessionCommand = { "type": "content", 
/**
 * The change.
 */
command: DocCommand, } | { "type": "follow" } | { "type": "setViewport", 
/**
 * The viewer.
 */
viewer: number, 
/**
 * The part of the video it shows; absent for the whole frame.
 */
viewport?: Viewport, } | { "type": "setOverlay", 
/**
 * The overlay.
 */
overlay: Overlay, 
/**
 * Whether it is on afterwards.
 */
shown: boolean, } | { "type": "control", 
/**
 * The change.
 */
control: ControlCommand, } | { "type": "requestTicket", 
/**
 * The stream.
 */
key: ContentRef, } | { "type": "releaseStream", 
/**
 * The viewer whose streams this seat releases.
 */
viewer: number, } | { "type": "workspace", 
/**
 * The change.
 */
command: WorkspaceCommand, };

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
