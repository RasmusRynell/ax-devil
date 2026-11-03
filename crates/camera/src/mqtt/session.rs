//! One connection to the broker: subscribe to the topic and forward what arrives until it fails.

use std::time::{Duration, SystemTime};

use rumqttc::{
    AsyncClient, ConnectReturnCode, ConnectionError, Event, MqttOptions, Packet, QoS, StateError,
    SubscribeReasonCode,
};

use super::Link;
use crate::error::{CameraError, ErrorKind, Refusal};
use crate::live::{Counters, Message, Status};

/// How often the client pings an idle broker; a broker that stops answering is lost after about this long.
const KEEP_ALIVE: Duration = Duration::from_secs(15);

/// The largest publication accepted: 4 MiB, well above an analytics frame. rumqttc's default of 10 KiB would make
/// an ordinary larger frame a connection error.
const MAX_INCOMING: usize = 4 * 1024 * 1024;
/// The largest packet this client sends: only a subscription, so rumqttc's own default of 10 KiB is plenty.
const MAX_OUTGOING: usize = 10 * 1024;

/// Connects to the broker, subscribes, and forwards messages until the connection fails (the error) or the app drops
/// its end (`Ok`). The subscription is live, and the epoch grows, once the broker has accepted it.
pub(super) async fn read(link: &mut Link) -> Result<(), CameraError> {
    let broker = &link.broker;
    let mut options = MqttOptions::new(link.reader_id.as_str(), broker.host.as_str(), broker.port);
    options
        .set_keep_alive(KEEP_ALIVE)
        .set_clean_session(true)
        .set_max_packet_size(MAX_INCOMING, MAX_OUTGOING);
    if let Some(login) = &broker.login {
        options.set_credentials(login.user.as_str(), login.password.expose());
    }
    let mut network = rumqttc::NetworkOptions::new();
    network.set_connection_timeout(link.timeout.as_secs().max(1));
    let (client, mut eventloop) = AsyncClient::new(options, 8);
    eventloop.set_network_options(network);
    // Queued now, sent as soon as the broker has accepted the connection.
    client
        .subscribe(link.topic.as_str(), QoS::AtMostOnce)
        .await
        .map_err(|_| CameraError::new(ErrorKind::Lost, "the MQTT client stopped"))?;

    let mut connected = false;
    loop {
        match eventloop.poll().await {
            Ok(Event::Incoming(Packet::ConnAck(_))) => connected = true,
            Ok(Event::Incoming(Packet::SubAck(ack))) => {
                if ack.return_codes.contains(&SubscribeReasonCode::Failure) {
                    return Err(CameraError::new(
                        ErrorKind::BrokerRefused(Refusal::NotAuthorized),
                        "the broker refused the subscription",
                    ));
                }
                let epoch = link.epoch.map_or(0, |last| last + 1);
                if epoch > 0 {
                    Counters::add(&link.counters.reconnects, 1);
                }
                link.epoch = Some(epoch);
                link.attempt = 0;
                link.status.send_replace(Status::Live { epoch });
            }
            Ok(Event::Incoming(Packet::Publish(publish))) => {
                let message = Message {
                    epoch: link.epoch.unwrap_or_default(),
                    topic: publish.topic,
                    payload: publish.payload,
                    received: SystemTime::now(),
                };
                if link.out.send(message, &link.counters).is_err() {
                    return Ok(());
                }
            }
            Ok(_) => {}
            Err(error) => return Err(broker_error(&error, connected)),
        }
    }
}

/// The error for a failure of the broker connection, from rumqttc's typed values; their text is never read. `connected`
/// is whether the broker had accepted the connection, which tells a connection that never came up from one that ended.
fn broker_error(error: &ConnectionError, connected: bool) -> CameraError {
    let ended = if connected {
        ErrorKind::Lost
    } else {
        ErrorKind::Unreachable
    };
    match error {
        ConnectionError::ConnectionRefused(code) => {
            let kind = match code {
                ConnectReturnCode::RefusedProtocolVersion => {
                    ErrorKind::BrokerRefused(Refusal::UnsupportedProtocol)
                }
                ConnectReturnCode::BadClientId => {
                    ErrorKind::BrokerRefused(Refusal::ClientIdRejected)
                }
                ConnectReturnCode::BadUserNamePassword => {
                    ErrorKind::BrokerRefused(Refusal::BadCredentials)
                }
                ConnectReturnCode::NotAuthorized => {
                    ErrorKind::BrokerRefused(Refusal::NotAuthorized)
                }
                // The broker is up but cannot serve now.
                ConnectReturnCode::ServiceUnavailable => ErrorKind::Unreachable,
                ConnectReturnCode::Success => ErrorKind::Malformed,
            };
            CameraError::new(kind, format!("the broker answered {code:?}"))
        }
        ConnectionError::Io(io) | ConnectionError::MqttState(StateError::Io(io)) => {
            CameraError::new(ended, io.kind().to_string())
        }
        ConnectionError::NetworkTimeout | ConnectionError::FlushTimeout => {
            CameraError::new(ErrorKind::TimedOut, "the broker did not answer in time")
        }
        ConnectionError::MqttState(StateError::ConnectionAborted | StateError::AwaitPingResp) => {
            CameraError::new(ended, "the broker connection ended")
        }
        // rumqttc cannot skip a packet it refuses to read, so it ends the connection. A publication is not sent
        // again (QoS 0), so reconnecting would not help the app and only repeat the failure for the next large
        // frame: end the subscription instead, and say why.
        ConnectionError::MqttState(StateError::Deserialization(
            rumqttc::Error::PayloadSizeLimitExceeded(_),
        )) => CameraError::new(
            ErrorKind::Unsupported,
            format!("the broker sent a publication larger than {MAX_INCOMING} bytes"),
        ),
        ConnectionError::NotConnAck(_) | ConnectionError::MqttState(_) => {
            CameraError::new(ErrorKind::Malformed, "the broker sent something unreadable")
        }
        _ => CameraError::new(ended, "the broker connection ended"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_refusal_is_typed_from_the_return_code() {
        use ConnectReturnCode::*;
        let table = [
            (
                RefusedProtocolVersion,
                ErrorKind::BrokerRefused(Refusal::UnsupportedProtocol),
            ),
            (
                BadClientId,
                ErrorKind::BrokerRefused(Refusal::ClientIdRejected),
            ),
            (
                BadUserNamePassword,
                ErrorKind::BrokerRefused(Refusal::BadCredentials),
            ),
            (
                NotAuthorized,
                ErrorKind::BrokerRefused(Refusal::NotAuthorized),
            ),
            (ServiceUnavailable, ErrorKind::Unreachable),
        ];
        for (code, kind) in table {
            let error = broker_error(&ConnectionError::ConnectionRefused(code), true);
            assert_eq!(error.kind, kind, "{code:?}");
            assert_eq!(error.kind.retryable(), code == ServiceUnavailable);
        }
    }

    #[test]
    fn an_oversized_publication_ends_the_subscription() {
        let error = ConnectionError::MqttState(StateError::Deserialization(
            rumqttc::Error::PayloadSizeLimitExceeded(MAX_INCOMING + 1),
        ));
        let error = broker_error(&error, true);
        assert_eq!(error.kind, ErrorKind::Unsupported);
        assert!(!error.kind.retryable());
    }

    #[test]
    fn a_connection_that_never_came_up_is_unreachable_and_one_that_ended_is_lost() {
        let error = ConnectionError::Io(std::io::ErrorKind::ConnectionRefused.into());
        assert_eq!(broker_error(&error, false).kind, ErrorKind::Unreachable);
        assert_eq!(broker_error(&error, true).kind, ErrorKind::Lost);
        let aborted = ConnectionError::MqttState(StateError::ConnectionAborted);
        assert_eq!(broker_error(&aborted, true).kind, ErrorKind::Lost);
    }
}
