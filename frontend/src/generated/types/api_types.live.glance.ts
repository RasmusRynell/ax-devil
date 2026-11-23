// Generated from api_types::live::glance by make; do not edit.

/**
 * The body of `POST /api/live/expansions`: the host texts of the Add Live Stream form as typed (blank takes the
 * configured default, whose references are the ones answered).
 */
export type ExpansionsRequest = { 
/**
 * The camera host.
 */
host?: string, 
/**
 * The MQTT broker host.
 */
broker_host?: string, };

/**
 * The body of `POST /api/live/check`: the stream whose own login is checked. The answer is the camera as a scan would
 * report it (`NetworkRow`), or no content when nothing is known: the stream is not resolved, the user has not started
 * or confirmed its host, its host is not an IPv4 address, it has no user, or no Axis camera answered.
 */
export type LiveCheckRequest = { 
/**
 * The workspace item.
 */
item: string, };

/**
 * What the references of the default connection texts show. A field is `null` unless its default is a `$NAME`
 * reference; a variable that is not set is an empty text, and a password only says whether it is set.
 */
export type LiveExpansions = { 
/**
 * The camera host: the typed one when it is a reference, else the default.
 */
host: string | null, 
/**
 * The default camera user.
 */
username: string | null, 
/**
 * Whether the default camera password is set.
 */
password_set: boolean | null, 
/**
 * The MQTT broker host: the typed one when it is a reference, else the default.
 */
broker_host: string | null, 
/**
 * The default broker user.
 */
broker_user: string | null, 
/**
 * Whether the default broker password is set.
 */
broker_pass_set: boolean | null, };
