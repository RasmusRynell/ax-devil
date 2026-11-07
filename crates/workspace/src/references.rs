//! What the references in a live stream's texts show, without resolving an item: the Add Live Stream form previews
//! the host and user a `$NAME` gives, and whether a password's variable is set.

use crate::{
    credentials::{Environment, Secret, reference},
    live::Expanded,
};

/// The three connection texts of a camera or a broker, as entered or as the settings give them.
#[derive(Clone, Copy)]
pub struct Connection<'a> {
    /// The host.
    pub host: &'a str,
    /// The user.
    pub user: &'a str,
    /// The password.
    pub password: &'a Secret,
}

/// What the references of one [`Connection`] show. A field is `None` unless its text is a `$NAME` reference; a variable
/// that is not set expands to an empty text. A password is never shown, only whether it is set.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Shown {
    /// The host's value.
    pub host: Option<String>,
    /// The user's value.
    pub user: Option<String>,
    /// Whether the password's variable is set.
    pub password_set: Option<bool>,
}

/// What the references of a camera's connection and of its MQTT broker's show.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Previews {
    /// The camera.
    pub camera: Shown,
    /// The broker, when the stream has one.
    pub broker: Shown,
}

fn shown(env: &Environment, connection: Connection<'_>) -> Shown {
    let expanded = Expanded::of(env, connection.host, connection.user, connection.password);
    Shown {
        host: reference(connection.host).map(|_| expanded.host),
        user: reference(connection.user).map(|_| expanded.user),
        password_set: connection
            .password
            .as_reference()
            .map(|_| !expanded.password.is_empty()),
    }
}

/// What `camera`'s and `broker`'s references show under `env`.
pub fn preview(
    env: &Environment,
    camera: Connection<'_>,
    broker: Option<Connection<'_>>,
) -> Previews {
    Previews {
        camera: shown(env, camera),
        broker: broker.map(|broker| shown(env, broker)).unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env() -> Environment {
        Environment::new(|name| match name {
            "AX_DEVIL_TARGET_ADDR" => Some("10.0.0.7".into()),
            "AX_DEVIL_TARGET_USER" => Some("root".into()),
            "AX_DEVIL_TARGET_PASS" => Some("s3cret".into()),
            _ => None,
        })
    }

    #[test]
    fn a_reference_shows_its_value_a_password_only_as_set_and_a_literal_nothing() {
        let password = Secret::new("$AX_DEVIL_TARGET_PASS");
        let shown = preview(
            &env(),
            Connection {
                host: "$AX_DEVIL_TARGET_ADDR",
                user: "$AX_DEVIL_TARGET_USER",
                password: &password,
            },
            Some(Connection {
                host: "broker.local",
                user: "$UNSET",
                password: &Secret::new("literal"),
            }),
        );
        assert_eq!(shown.camera.host.as_deref(), Some("10.0.0.7"));
        assert_eq!(shown.camera.user.as_deref(), Some("root"));
        assert_eq!(shown.camera.password_set, Some(true));
        assert!(!format!("{shown:?}").contains("s3cret"));
        assert_eq!(shown.broker.host, None);
        assert_eq!(shown.broker.user.as_deref(), Some(""), "unset is empty");
        assert_eq!(shown.broker.password_set, None);
    }
}
