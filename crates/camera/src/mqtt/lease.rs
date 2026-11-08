//! The lease on the camera's MQTT side: what a subscription changes on the camera, and giving it all back.
//!
//! Acquiring saves the camera's MQTT client state, points the client at the broker, and reuses a matching analytics
//! publisher or creates one. Releasing removes only a publisher this lease created and restores the saved state. Every
//! step is recorded before it is taken, so a lease whose acquisition was cut short still releases what it did.
//!
//! Crash recovery: a camera whose MQTT client already carries this crate's client id (or the Python client's it
//! replaces) was left by an earlier run that never released it. The original configuration is lost, so that run's
//! configuration is taken over, and release leaves the client inactive instead of refusing the camera for good.

use serde_json::{Value, json};
use tokio::time::sleep;
use tracing::warn;

use crate::device::Device;
use crate::error::{CameraError, ErrorKind};
use crate::logtext::escaped;
use crate::mqtt::{CLIENT_PREFIX, LEGACY_CLIENT_PREFIX, Publisher};
use crate::target::Broker;
use crate::vapix::SourceKey;

/// What a lease points the camera at.
pub(crate) struct Plan {
    pub(crate) broker: Broker,
    /// The camera's MQTT client id.
    pub(crate) client_id: String,
    /// The publisher wanted; its `id` is replaced when a new one is created.
    pub(crate) publisher: Publisher,
}

pub(crate) struct Lease {
    device: Device,
    plan: Plan,
    /// The camera's MQTT client state before this lease touched it.
    saved: Option<Value>,
    /// The id of the publisher this lease created, if it did.
    created: Option<String>,
    /// Whether the camera may still be applying the request that creates `created`: the request failed without an
    /// answer, so an early removal could run before the publisher exists.
    uncertain: bool,
    held: bool,
}

impl Lease {
    pub(crate) fn new(device: Device, plan: Plan) -> Self {
        Self {
            device,
            plan,
            saved: None,
            created: None,
            uncertain: false,
            held: false,
        }
    }

    pub(crate) fn held(&self) -> bool {
        self.held
    }

    /// Takes the lease. When that fails, whatever was changed is given back before the error is returned.
    pub(crate) async fn acquire(&mut self) -> Result<(), CameraError> {
        match self.configure().await {
            Ok(()) => {
                self.held = true;
                Ok(())
            }
            Err(error) => {
                // The failure to report is the first one; a failure to clean up is only logged.
                let _ = self.release().await;
                Err(error)
            }
        }
    }

    async fn configure(&mut self) -> Result<(), CameraError> {
        // A data source the camera does not offer is refused before the camera is touched.
        let sources = self.device.analytics_sources().await?;
        if !sources.contains(&self.plan.publisher.source) {
            warn!(
                "{}",
                unavailable_source(
                    &self.plan.publisher.source.0,
                    &self.device.camera.host.to_string(),
                    &sources
                )
            );
            return Err(CameraError::new(
                ErrorKind::NotFound,
                format!(
                    "the camera offers no analytics data source {}",
                    self.plan.publisher.source.0
                ),
            ));
        }
        if self.saved.is_none() {
            let state = self.device.mqtt_client_state().await?;
            self.saved = Some(restorable(state)?);
        }
        self.device
            .mqtt_client_configure(&client_config(&self.plan))
            .await?;
        let publishers = self.device.analytics_publishers().await?;
        let wanted = &self.plan.publisher;
        let reusable = publishers.iter().any(|found| {
            found.topic == wanted.topic
                && found.source == wanted.source
                && found.qos == wanted.qos
                && found.retain == wanted.retain
                && found.use_topic_prefix == wanted.use_topic_prefix
        });
        if !reusable {
            let id = super::unique(&self.plan.client_id);
            // Recorded before the request: if the task is cut off while it is in flight, release still removes it.
            self.created = Some(id.clone());
            let publisher = Publisher {
                id,
                ..wanted.clone()
            };
            if let Err(error) = self.device.create_publisher(&publisher).await {
                self.uncertain = error.kind.retryable();
                return Err(error);
            }
        }
        self.device.mqtt_client_activate(true).await
    }

    /// Gives back what the lease changed, as far as the camera answers. A record of a change is kept until the call
    /// that undoes it has succeeded, so a failed release can be tried again and never mistakes a changed
    /// configuration for the original. The first failure is returned, saying what may be left, and every failure is
    /// logged.
    pub(crate) async fn release(&mut self) -> Result<(), CameraError> {
        self.held = false;
        let mut first: Option<CameraError> = None;
        if let Some(id) = self.created.clone() {
            if self.uncertain {
                // A camera that is still applying a request we gave up on is assumed to finish within one more
                // timeout; removing sooner could succeed before the publisher exists.
                sleep(self.device.camera.timeout).await;
            }
            match self.device.remove_publisher(&id).await {
                // Already gone is as good as removed.
                Ok(()) => (self.created, self.uncertain) = (None, false),
                Err(error) if error.kind == ErrorKind::NotFound => {
                    (self.created, self.uncertain) = (None, false);
                }
                Err(error) => first = Some(error),
            }
        }
        if let Some(saved) = self.saved.clone() {
            match self.restore(&saved).await {
                Ok(()) => self.saved = None,
                Err(error) => {
                    first.get_or_insert(error);
                }
            }
        }
        let Some(error) = first else { return Ok(()) };
        let mut left = Vec::new();
        if let Some(id) = &self.created {
            left.push(format!("publisher {id} may remain"));
        }
        if self.saved.is_some() {
            left.push("the client settings may differ".to_owned());
        }
        let detail = format!(
            "the camera's MQTT state was not restored ({}): {}",
            left.join(", "),
            error.detail()
        );
        warn!(kind = ?error.kind, "{detail}");
        Err(CameraError::new(error.kind, detail))
    }

    async fn restore(&self, saved: &Value) -> Result<(), CameraError> {
        let config = &saved["config"];
        if config.as_object().is_some_and(|config| !config.is_empty()) {
            self.device.mqtt_client_configure(config).await?;
        } else {
            // There was no configuration to give back, and deactivating alone would leave the broker login written
            // at acquisition on the camera. The API has no call that clears a configuration, so replace it with
            // the lease's own, without the login; the client is left inactive below.
            let mut bare = client_config(&self.plan);
            if let Some(config) = bare.as_object_mut() {
                config.remove("username");
                config.remove("password");
            }
            self.device.mqtt_client_configure(&bare).await?;
        }
        self.device
            .mqtt_client_activate(saved["status"]["state"] == "active")
            .await
    }
}

/// The state to give back on release. Refuses a camera whose MQTT client has a password: the camera reports it masked
/// (`*****`), so it cannot be put back after the lease replaces it, and the camera's own broker connection would be
/// left broken. The exception is a leftover of this crate or the Python client (see the module doc): its state to
/// give back is an inactive client.
fn restorable(state: Value) -> Result<Value, CameraError> {
    let has_password = state["config"]["password"]
        .as_str()
        .is_some_and(|password| !password.is_empty());
    let ours = state["config"]["clientId"]
        .as_str()
        .is_some_and(|id| id.starts_with(CLIENT_PREFIX) || id.starts_with(LEGACY_CLIENT_PREFIX));
    if ours {
        return Ok(json!({ "status": { "state": "inactive" }, "config": {} }));
    }
    if has_password {
        return Err(CameraError::new(
            ErrorKind::Unrestorable,
            "the camera already has an authenticated MQTT client configuration, whose password cannot be read back \
             and restored; it was left unchanged",
        ));
    }
    Ok(state)
}

/// The camera's MQTT client configuration for `plan`; the password is sent to the camera and goes nowhere else. A
/// broker without a login has no `username` or `password` key at all, rather than null ones.
fn client_config(plan: &Plan) -> Value {
    let broker = &plan.broker;
    let mut config = json!({
        "server": { "host": broker.host.as_str(), "port": broker.port, "protocol": "tcp" },
        "keepAliveInterval": 60,
        "clientId": plan.client_id,
        "cleanSession": true,
        "autoReconnect": true,
    });
    if let Some(login) = &broker.login {
        config["username"] = login.user.as_str().into();
        config["password"] = login.password.expose().into();
    }
    config
}

/// How many sources the log line for a missing one names.
const SUGGESTED: usize = 8;

/// The log line for a data source the camera does not offer: the sources on the same `#channel` when there are any,
/// else all of them, at most [`SUGGESTED`] with the rest counted, or `none`. It names the host and sources, no login.
fn unavailable_source(wanted: &str, host: &str, available: &[SourceKey]) -> String {
    let channel = wanted
        .rsplit_once('#')
        .map_or(wanted, |(_, channel)| channel);
    let all: Vec<&str> = available.iter().map(|source| source.0.as_str()).collect();
    let same_channel: Vec<&str> = all
        .iter()
        .copied()
        .filter(|key| !channel.is_empty() && key.ends_with(&format!("#{channel}")))
        .collect();
    let relevant = if same_channel.is_empty() {
        all
    } else {
        same_channel
    };
    let mut listed = match relevant.len() {
        0 => "none".to_owned(),
        count => relevant[..count.min(SUGGESTED)]
            .iter()
            .map(|key| escaped(key))
            .collect::<Vec<_>>()
            .join(", "),
    };
    let (wanted, host) = (escaped(wanted), escaped(host));
    if relevant.len() > SUGGESTED {
        listed = format!("{listed} (+{} more)", relevant.len() - SUGGESTED);
    }
    format!(
        "Analytics data source '{wanted}' is not available on device {host}. Available sources: {listed}. \
         Choose a source in Add Live Stream, or pass --data-source with a matching --handler-type."
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn listed(wanted: &str, available: &[&str]) -> String {
        let available: Vec<SourceKey> = available
            .iter()
            .map(|key| SourceKey((*key).into()))
            .collect();
        let message = unavailable_source(wanted, "cam.local", &available);
        let (_, rest) = message.split_once("Available sources: ").expect("the list");
        rest.split_once(". Choose").expect("its end").0.to_owned()
    }

    #[test]
    fn the_sources_named_are_those_of_the_same_channel_at_most_eight() {
        let many: Vec<String> = (0..11).map(|n| format!("topic{n}#2")).collect();
        let many: Vec<&str> = many.iter().map(String::as_str).collect();
        let eight =
            "topic0#2, topic1#2, topic2#2, topic3#2, topic4#2, topic5#2, topic6#2, topic7#2";
        for (wanted, available, expected) in [
            ("a#2", &["a#1", "b#2", "c#3"][..], "b#2".to_owned()),
            ("a#9", &["a#1", "b#2"][..], "a#1, b#2".to_owned()),
            ("a#2", &[][..], "none".to_owned()),
            (
                "a#2",
                &["x\n2026 ERROR forged#2"][..],
                "x\\n2026 ERROR forged#2".to_owned(),
            ),
            ("a#2", &many[..], format!("{eight} (+3 more)")),
            ("a#2", &many[..8], eight.to_owned()),
        ] {
            assert_eq!(
                listed(wanted, available),
                expected,
                "{wanted} in {available:?}"
            );
        }
    }
}
