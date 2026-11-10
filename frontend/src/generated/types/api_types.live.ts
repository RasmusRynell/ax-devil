// Generated from api_types::live by make; do not edit.

import type { CatalogInfoWire } from "./api_types.catalogs";
import type { LaneDefaultsWire, LaneInfo } from "./api_types.playback.lane";
import type { OperationKindWire } from "./scene.scene";

/**
 * What a holder of a live stream asks of its feed.
 */
export type LiveAction = "pause" | "resume" | "retry";

/**
 * One change to object identity, as a line of the Events list.
 */
export type LiveEventWire = { 
/**
 * Which kind of change.
 */
kind: OperationKindWire, 
/**
 * A one-line description such as `Rename 12 → 57`.
 */
label: string, 
/**
 * The objects involved: the ones changed first, then the ones they become.
 */
entities: Array<string>, };

/**
 * Events a released frame made visible: those of every sample not reported before, up to the one it shows.
 */
export type LiveEventsWire = { 
/**
 * The frame that made them visible, by sequence id.
 */
frame: number, 
/**
 * The samples with events, oldest first.
 */
samples: Array<LiveSampleEventsWire>, };

/**
 * Where one feed (the video, or the overlay) stands.
 */
export type LiveFeedStatus = { "status": "Connecting" } | { "status": "Live" } | { "status": "Stalled", 
/**
 * How many seconds of silence make a feed stalled.
 */
seconds: number, } | { "status": "Reconnecting", 
/**
 * Which retry this is, from 1; reset when data flows again.
 */
attempt: number, 
/**
 * Why the last attempt failed.
 */
reason: LiveReason, } | { "status": "Failed", 
/**
 * Why.
 */
reason: LiveReason, };

/**
 * Where samples were lost: the overlay's decoder was behind and was reset. The Events list shows a marker.
 */
export type LiveGapWire = { 
/**
 * When the loss began, on the video clock in microseconds.
 */
time_us: number, };

/**
 * The answer to `Open` for a live stream; the first message of its socket.
 */
export type LiveOpenedWire = { 
/**
 * The protocol version, to compare with `PLAYBACK_PROTOCOL`.
 */
protocol: number, 
/**
 * The one lane: the overlay's, or a plain lane when the video has none. Its position in lane messages is 0.
 */
lane: LaneInfo, 
/**
 * What the lane starts with.
 */
lane_defaults: LaneDefaultsWire, 
/**
 * The catalogs the lane may draw with, built-ins first and Standard first among them.
 */
catalogs: Array<CatalogInfoWire>, };

/**
 * Where a live video's overlay comes from. At most one per video.
 */
export type LiveOverlayKind = "none" | "rtsp" | "mqtt" | "datahub";

/**
 * The protocol of a camera's web interface.
 */
export type LiveProtocol = "http" | "https";

/**
 * Why a feed is not live: the kind of the camera error, as a stable code the client words. Never the device's own
 * text, which can name the host.
 */
export type LiveReason = "LoginRequired" | "LoginRejected" | "Forbidden" | "NotAxis" | "NotFound" | "Unsupported" | "SourceUnavailable" | "BrokerUnsupportedProtocol" | "BrokerClientIdRejected" | "BrokerBadCredentials" | "BrokerNotAuthorized" | "Busy" | "Unrestorable" | "InvalidTarget" | "Unreachable" | "TimedOut" | "Lost" | "Silent" | "Malformed" | "Throttled" | "DeviceFault";

/**
 * Which door of the camera a stream comes through.
 */
export type LiveRtspRoute = "direct" | "web_api";

/**
 * The events of one sample.
 */
export type LiveSampleEventsWire = { 
/**
 * The sample's time on the video clock, in microseconds.
 */
time_us: number, 
/**
 * Its events, in the Scene's order; never empty.
 */
events: Array<LiveEventWire>, };

/**
 * Where the feeds stand. Sent when any of it changes.
 */
export type LiveStatusWire = { 
/**
 * The video.
 */
video: LiveFeedStatus, 
/**
 * The overlay; `null` when the video has none.
 */
overlay: LiveFeedStatus | null, 
/**
 * Whether the overlay's decoder is behind: its queue overflowed and holds until it drains. The Events list gets a
 * `LiveGap` for it.
 */
behind: boolean, 
/**
 * Whether the overlay is up but has delivered nothing on this connection yet; frames do not wait for it meanwhile.
 */
waiting: boolean, };
