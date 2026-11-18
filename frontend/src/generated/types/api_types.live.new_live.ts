// Generated from api_types::live::new_live by make; do not edit.

import type { KeptDataHubWire, KeptMqttWire } from "./api_types.live.kept";
import type { LiveProtocol, LiveRtspRoute } from "./workspace.live";

/**
 * The overlay the user chose for a new stream; anything left out takes the configured default. Mirrors
 * `workspace::intake::NewLiveOverlay`.
 */
export type NewLiveOverlayWire = { "mode": "none" } | { "mode": "rtsp", 
/**
 * The decoder id.
 */
handler?: string, } | { "mode": "mqtt", 
/**
 * The decoder id.
 */
handler?: string, 
/**
 * The broker host.
 */
broker_host?: string, 
/**
 * The broker port, checked against 1 to 65535.
 */
broker_port?: number, 
/**
 * The broker user.
 */
broker_user?: string, 
/**
 * The broker password.
 */
broker_pass?: WireSecret, 
/**
 * The camera's analytics data source.
 */
source_key?: string, } | { "mode": "datahub", 
/**
 * The decoder id.
 */
handler?: string, 
/**
 * The topic.
 */
topic?: string, 
/**
 * The topic's instance, from 1.
 */
channel?: number, };

/**
 * What the user entered to add a live stream. Text that is absent or blank takes its configured default; numbers are
 * checked against their range by the intake, so its message names the field. Mirrors `workspace::intake::NewLive`.
 */
export type NewLiveWire = { 
/**
 * The name; the host when empty.
 */
label?: string, 
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
 * The RTSP port, 1 to 65535.
 */
rtsp_port?: number, 
/**
 * Which door of the camera the stream comes through; absent is the configured one.
 */
rtsp_route?: LiveRtspRoute, 
/**
 * The resolution such as `1280x720`; blank is the camera's own.
 */
resolution?: string, 
/**
 * A stream URL to use instead of the one made from the host, as typed. A saved URL is sent without its login's
 * password, and one sent back that way keeps the saved password.
 */
url?: string, 
/**
 * The overlay; absent is the configured one for a new stream and the saved one for an edit.
 */
overlay?: NewLiveOverlayWire, 
/**
 * What the form holds for MQTT while another overlay mode is chosen, kept with the stream.
 */
kept_mqtt?: KeptMqttWire, 
/**
 * What the form holds for DataHub while another overlay mode is chosen, kept with the stream.
 */
kept_datahub?: KeptDataHubWire, 
/**
 * The address of a camera the network scan signed in to. The backend then takes the host, protocol, port and
 * login from the scan, so the login never reaches the client, and the fields above fill in the rest. Only the
 * session's owner may add such a stream. Never part of a stored recipe.
 */
found?: string, 
/**
 * A live stream this one is another of. The backend fills a blank user and password from that stream's saved
 * login, so a literal password never reaches the client, and only while the host, protocol and port are the
 * same as that stream's and the seat is the session's owner. Never part of a stored recipe.
 */
like?: string, };

/**
 * A password in a request. `Debug` prints `***`, so a logged request cannot leak it.
 */
export type WireSecret = string;
