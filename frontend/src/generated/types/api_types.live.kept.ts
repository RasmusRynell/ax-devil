// Generated from api_types::live::kept by make; do not edit.

import type { WireSecret } from "./api_types.live.new_live";

/**
 * The DataHub settings of a stream whose overlay is another mode. Mirrors `workspace::DataHubSettings`.
 */
export type KeptDataHubWire = { 
/**
 * The topic.
 */
topic?: string, 
/**
 * The topic's instance.
 */
channel?: number, };

/**
 * The MQTT settings of a stream whose overlay is another mode. Mirrors `workspace::MqttSettings`; what is absent takes
 * the configured default.
 */
export type KeptMqttWire = { 
/**
 * The broker host.
 */
broker_host?: string, 
/**
 * The broker port.
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
source_key?: string, };
