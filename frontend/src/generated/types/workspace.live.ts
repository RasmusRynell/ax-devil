// Generated from workspace::live by make; do not edit.

/**
 * The protocol of the camera's web interface and DataHub.
 */
export type LiveProtocol = "http" | "https";

/**
 * Which door of the camera a stream comes through, when it has no stream URL of its own (the URL's scheme decides).
 */
export type LiveRtspRoute = "direct" | "web_api";
