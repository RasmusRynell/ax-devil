// Generated from api_types::live::discovery by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { LiveProtocol, LiveReason } from "./api_types.live";
import type { WireSecret } from "./api_types.live.new_live";

/**
 * The camera a question is about. Anything left out takes the configured default, and text may be a `$NAME`
 * reference, which expands under the same rule as the references of a stream.
 */
export type LiveConnection = { 
/**
 * The camera host or a `$NAME` reference.
 */
host?: string, 
/**
 * The camera user or a reference.
 */
username?: string, 
/**
 * The camera password or a reference.
 */
password?: WireSecret, 
/**
 * The web interface's protocol.
 */
protocol?: LiveProtocol, 
/**
 * The web interface's port, 0 to 65535; 0 or absent is the standard one.
 */
port?: number, 
/**
 * The video channel, from 1.
 */
head?: number, 
/**
 * A live stream this connection is another of. The backend fills a blank user and password from that stream's
 * saved login, as it does when the stream is added (see `NewLiveWire::like`), so the question authenticates as
 * the stream will and no literal password reaches the client.
 */
like?: string, };

/**
 * The stream sizes one camera head lists of its own.
 */
export type LiveHeadResolutions = { 
/**
 * The video channel, from 1.
 */
head: number, 
/**
 * The sizes, in the camera's order; none when the head's list holds no valid size.
 */
resolutions: Array<LiveResolution>, };

/**
 * Why a question to a camera got no answer: the body of a failed `POST /api/live/resolutions` or
 * `POST /api/live/sources`.
 */
export type LiveProblem = { "problem": "Invalid", 
/**
 * What to fix.
 */
error: ErrorWire, } | { "problem": "Camera", 
/**
 * Why, as a code the client words.
 */
reason: LiveReason, };

/**
 * A stream size a camera offers.
 */
export type LiveResolution = { 
/**
 * Width in pixels.
 */
width: number, 
/**
 * Height in pixels.
 */
height: number, };

/**
 * The stream sizes of every head of a camera, the answer of the resolutions route. A head takes its own list when
 * `heads` has one and the camera-wide `common` list otherwise.
 */
export type LiveResolutions = { 
/**
 * Whether the camera listed any image parameter. An answer without one is worth asking for again.
 */
listed: boolean, 
/**
 * The camera-wide sizes, in the camera's order.
 */
common: Array<LiveResolution>, 
/**
 * The heads with a list of their own.
 */
heads: Array<LiveHeadResolutions>, };

/**
 * The analytics data sources a camera publishes, the answer of the sources route.
 */
export type LiveSources = { 
/**
 * The sources' keys, such as `com.axis.scene.frame.v1#1`.
 */
sources: Array<string>, };

/**
 * The DataHub topics a camera offers.
 */
export type LiveTopics = { 
/**
 * The topics' names.
 */
topics: Array<string>, };

/**
 * What an Axis camera said about itself without a login.
 */
export type NetworkIdentity = { 
/**
 * The product name, such as `Q1656`.
 */
model: string, 
/**
 * The serial number.
 */
serial: string, 
/**
 * The firmware version.
 */
firmware: string, 
/**
 * The product type, such as `Box Camera`.
 */
product_type: string, };

/**
 * An Axis camera the network scan found. Only the owner of a backend with the files grant sees these.
 */
export type NetworkRow = { 
/**
 * Its IPv4 address.
 */
address: string, 
/**
 * The web protocol that answered.
 */
scheme: LiveProtocol, 
/**
 * The web port that answered.
 */
port: number, 
/**
 * What it said about itself.
 */
identity: NetworkIdentity, 
/**
 * How long it has been up, in seconds, if it said.
 */
uptime_s: number | null, 
/**
 * The outcome of signing in.
 */
sign_in: NetworkSignIn, };

/**
 * Whether the scan signed in to a camera. No password is in any of these.
 */
export type NetworkSignIn = { "state": "NotTried" } | { "state": "SignedIn", 
/**
 * Which of the scan's logins, by position in the `scan.logins` setting.
 */
login_index: number, 
/**
 * The architecture, such as `aarch64`; empty if the camera did not say.
 */
architecture: string, 
/**
 * The system on chip; empty if the camera did not say.
 */
soc: string, } | { "state": "Refused" } | { "state": "Failed" };

/**
 * The cameras the scan has found so far; one line of the `GET /api/network` stream.
 */
export type NetworkSnapshot = { 
/**
 * Counts the scans started; rows of an older scan are never shown with a newer number.
 */
scan: number, 
/**
 * Whether the scan is still running.
 */
scanning: boolean, 
/**
 * The cameras, by address.
 */
rows: Array<NetworkRow>, 
/**
 * Why the scan stopped before it was over, with the cameras found until then in `rows`; `null` when it ended as
 * it should or was stopped. Free text for whoever may watch scans.
 */
failure: string | null, };

/**
 * The body of `POST /api/live/resolutions`.
 */
export type ResolutionsRequest = { 
/**
 * The camera and head to ask.
 */
connection: LiveConnection, };

/**
 * The body of `POST /api/network/scan`.
 */
export type ScanRequestWire = { 
/**
 * The addresses and ranges to scan, such as `192.168.1.0/24`. Blank is the local network; absent is the
 * `scan.range` setting.
 */
range?: string, 
/**
 * Whether to sign in to the cameras found, for this scan only; absent is the `scan.sign_in` setting.
 */
sign_in?: boolean, };

/**
 * The answer of `POST /api/network/scan`.
 */
export type ScanStarted = { 
/**
 * The number of the scan started; the `scan` of its snapshots, and of every later scan's.
 */
scan: number, };

/**
 * The answer of `POST /api/live/sources`: the list the request's overlay asked for.
 */
export type SourcesAnswer = { "overlay": "mqtt" } & LiveSources | { "overlay": "datahub" } & LiveTopics;

/**
 * What kind of overlay the sources are for.
 */
export type SourcesOverlay = "mqtt" | "datahub";

/**
 * The body of `POST /api/live/sources`.
 */
export type SourcesRequest = { 
/**
 * The camera to ask.
 */
connection: LiveConnection, 
/**
 * Which list to return.
 */
overlay: SourcesOverlay, };
