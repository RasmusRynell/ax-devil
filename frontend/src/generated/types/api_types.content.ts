// Generated from api_types::content by make; do not edit.

import type { LiveOverlayKind } from "./api_types.live";
import type { NewLiveWire } from "./api_types.live.new_live";

/**
 * Content by kind. Viewers register by kind.
 */
export type ContentInfo = { "kind": "SeekableVideo", 
/**
 * Names the Content; a ticket request names the stream by this id, `generation` and the entry's id.
 */
id: string, 
/**
 * The commit of the Content's resolution. Re-resolving or reopening the item gives a new one, which makes
 * tickets for the old one worthless.
 */
generation: number, 
/**
 * The name the viewer shows.
 */
display_name: string, 
/**
 * The one entry: the video and its lanes.
 */
entry: EntryInfo, } | { "kind": "Playlist", 
/**
 * Names the Content.
 */
id: string, 
/**
 * The commit of the Content's resolution, as for a video.
 */
generation: number, 
/**
 * The name the viewer shows.
 */
display_name: string, 
/**
 * The entries of every playlist, in order.
 */
entries: Array<EntryInfo>, 
/**
 * The playlists, each covering the next run of `entries`; at least one.
 */
playlists: Array<PlaylistSpanInfo>, } | { "kind": "LiveVideo", 
/**
 * Names the Content and its one stream.
 */
id: string, 
/**
 * The commit of the Content's resolution, as for a video.
 */
generation: number, 
/**
 * The name the viewer shows: the user's label, or `Live stream`.
 */
display_name: string, 
/**
 * The lane's slot, which names it across resolutions.
 */
slot: string, 
/**
 * Where the overlay comes from.
 */
overlay: LiveOverlayKind, 
/**
 * The lane names: the overlay's, or the display name for the plain lane of a video without one.
 */
lanes: Array<string>, 
/**
 * The request that would make this stream again, as stored: `$NAME` references stay references and a literal
 * password is left out. Only for an audience with host details; `null` for everyone else.
 */
recipe: NewLiveWire | null, };

/**
 * One video and its lanes: what a viewer opens.
 */
export type EntryInfo = { 
/**
 * Names the entry; a ticket request names it by this id. A video's one entry has the Content's id.
 */
id: string, 
/**
 * The name rows and viewers show.
 */
label: string, 
/**
 * The lane names, in order.
 */
lanes: Array<string>, 
/**
 * The decoder id of each overlay lane, in order; empty for an entry without overlays.
 */
handlers: Array<string>, 
/**
 * What the resolver states about the entry: key and text rows, keys sorted.
 */
metadata: Array<[string, string]>, 
/**
 * What the resolver states about each overlay lane, as `metadata`, in the order of `handlers`.
 */
lane_metadata: Array<Array<[string, string]>>, };

/**
 * One playlist of a playlist item: a run of its entries a viewer steps within.
 */
export type PlaylistSpanInfo = { 
/**
 * Names the playlist.
 */
id: string, 
/**
 * What rows and the viewer's header show.
 */
label: string, 
/**
 * How many entries it holds; the first playlist starts at the first entry.
 */
entries: number, 
/**
 * How many matches the resolver found, when it says.
 */
matches?: number, 
/**
 * How many sequence groups the resolver found, when it says.
 */
sequence_groups?: number, 
/**
 * What the resolver states about the playlist: key and text rows, keys sorted.
 */
metadata: Array<[string, string]>, };
