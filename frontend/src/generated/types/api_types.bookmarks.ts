// Generated from api_types::bookmarks by make; do not edit.

import type { BookmarkKind } from "./workspace.bookmark";

/**
 * A change to the bookmarks or reviewed marks.
 */
export type BookmarkCommand = { "type": "add", 
/**
 * The item.
 */
item: string, 
/**
 * The entry marked, by its id: the item's own id for a video.
 */
entry: string, 
/**
 * The frame's index.
 */
frame: number, 
/**
 * The frame's time in microseconds, not negative.
 */
timeUs: number, } | { "type": "edit", 
/**
 * The bookmark.
 */
id: string, 
/**
 * The new text; unchanged when absent.
 */
note?: string, 
/**
 * The new kind; unchanged when absent.
 */
kind?: BookmarkKind, } | { "type": "remove", 
/**
 * The bookmark.
 */
id: string, } | { "type": "setReviewed", 
/**
 * The item.
 */
item: string, 
/**
 * The entry as its group names it; absent for a video.
 */
entry?: string, 
/**
 * Whether it is reviewed afterwards.
 */
reviewed: boolean, };

/**
 * A video or playlist entry with bookmarks or a reviewed mark.
 */
export type BookmarkGroupInfo = { 
/**
 * Names the group.
 */
key: string, 
/**
 * The item.
 */
item: string, 
/**
 * The item's name.
 */
item_name: string, 
/**
 * The entry as shown under the item; empty for a video.
 */
entry: string, 
/**
 * The entry as `setReviewed` names it; absent for a video.
 */
target: string | null, 
/**
 * Whether it is marked reviewed.
 */
reviewed: boolean, 
/**
 * Whether it can be opened.
 */
standing: BookmarkStanding, 
/**
 * The bookmarks by frame.
 */
bookmarks: Array<BookmarkInfo>, };

/**
 * One bookmark.
 */
export type BookmarkInfo = { 
/**
 * Names the bookmark.
 */
id: string, 
/**
 * The frame's index.
 */
frame: number, 
/**
 * The frame's time in microseconds.
 */
time_us: number, 
/**
 * What it marks.
 */
kind: BookmarkKind, 
/**
 * The user's text; empty when there is none.
 */
note: string, };

/**
 * Whether a group can be opened.
 */
export type BookmarkStanding = { "status": "Open", 
/**
 * The entry's id.
 */
entry: string, } | { "status": "Pending" } | { "status": "Missing" };
