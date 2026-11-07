//! The flags of `live`. Each flag overrides the stream setting it names; the intake builds the item and fills the rest
//! from the settings.

use clap::{Args, ValueEnum};
use workspace::{
    Protocol, Secret,
    intake::{LiveDefaults, LiveMode, NewDataHub, NewLive, NewLiveOverlay, NewMqtt},
};

/// The overlay a `live` command asks for; `websocket` is the DataHub overlay.
#[derive(Clone, Copy, ValueEnum)]
pub(super) enum LiveOverlayArg {
    None,
    Rtsp,
    Mqtt,
    Websocket,
}

/// The protocol the device API is reached by.
#[derive(Clone, Copy, ValueEnum)]
pub(super) enum LiveProtocolArg {
    Https,
    Http,
}

/// The flags of `live`.
#[derive(Args)]
pub(super) struct LiveArgs {
    /// The device host.
    #[arg(long, value_name = "HOST")]
    host: Option<String>,
    /// The device username.
    #[arg(long, value_name = "USERNAME")]
    username: Option<String>,
    /// The device password.
    #[arg(long, value_name = "PASSWORD")]
    password: Option<String>,
    /// The camera head number.
    #[arg(long, value_name = "NUMBER")]
    camera_head: Option<i64>,
    /// The video resolution, such as 1280x720 (default: camera default).
    #[arg(long, value_name = "WIDTHxHEIGHT")]
    resolution: Option<String>,
    /// The overlay; `websocket` is a DataHub topic.
    #[arg(long, value_name = "OVERLAY")]
    overlay: Option<LiveOverlayArg>,
    /// The decoder of the overlay.
    #[arg(long, value_name = "HANDLER")]
    handler_type: Option<String>,
    /// The MQTT broker host.
    #[arg(long, value_name = "HOST")]
    mqtt_host: Option<String>,
    /// The MQTT broker port.
    #[arg(long, value_name = "PORT")]
    mqtt_port: Option<i64>,
    /// The MQTT broker username.
    #[arg(long, value_name = "USERNAME")]
    mqtt_username: Option<String>,
    /// The MQTT broker password.
    #[arg(long, value_name = "PASSWORD")]
    mqtt_password: Option<String>,
    /// The analytics data source of the MQTT overlay.
    #[arg(long, value_name = "KEY")]
    data_source: Option<String>,
    /// The protocol of the device API.
    #[arg(long, value_name = "PROTOCOL", ignore_case = true)]
    device_api_protocol: Option<LiveProtocolArg>,
    /// The DataHub WebSocket topic.
    #[arg(long, value_name = "TOPIC")]
    topic: Option<String>,
    /// The DataHub topic instance ID.
    #[arg(long, value_name = "ID")]
    channel_id: Option<i64>,
}

impl LiveArgs {
    /// The stream these flags ask for. The overlay is the one `--overlay` names, else the one the settings name, and
    /// the overlay flags that apply to it are the ones given; the rest of the item comes from `defaults`.
    pub(super) fn into_new_live(self, defaults: &LiveDefaults) -> NewLive {
        let mode = self.overlay.map_or(defaults.overlay, mode_of);
        let overlay = match mode {
            LiveMode::None => NewLiveOverlay::None,
            LiveMode::Rtsp => NewLiveOverlay::Rtsp {
                handler: self.handler_type,
            },
            LiveMode::Mqtt => NewLiveOverlay::Mqtt(NewMqtt {
                handler: self.handler_type,
                broker_host: self.mqtt_host,
                broker_port: self.mqtt_port,
                broker_user: self.mqtt_username,
                broker_pass: self.mqtt_password.map(Secret::new),
                source_key: self.data_source,
            }),
            LiveMode::DataHub => NewLiveOverlay::DataHub(NewDataHub {
                handler: self.handler_type,
                topic: self.topic,
                channel: self.channel_id,
            }),
        };
        NewLive {
            host: self.host,
            username: self.username,
            password: self.password.map(Secret::new),
            protocol: self.device_api_protocol.map(|protocol| match protocol {
                LiveProtocolArg::Https => Protocol::Https,
                LiveProtocolArg::Http => Protocol::Http,
            }),
            head: self.camera_head,
            resolution: self.resolution,
            overlay: Some(overlay),
            ..NewLive::default()
        }
    }
}

/// The overlay mode a `--overlay` value names.
fn mode_of(overlay: LiveOverlayArg) -> LiveMode {
    match overlay {
        LiveOverlayArg::None => LiveMode::None,
        LiveOverlayArg::Rtsp => LiveMode::Rtsp,
        LiveOverlayArg::Mqtt => LiveMode::Mqtt,
        LiveOverlayArg::Websocket => LiveMode::DataHub,
    }
}
