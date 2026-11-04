//! What the references in a live stream's texts allow, without resolving an item: which may be shown to the person
//! who set them, and which hosts the user's own configuration names. Both follow the binding rule of the resolution
//! (`docs/rebuild/13-security-packaging-ops.md#threat-model`): a reference counts only with its bound host or for a host
//! the user confirmed.

use crate::{
    credentials::{Environment, Secret, reference},
    live::{BROKER_NAMES, CAMERA_NAMES, Endpoint, Names},
};
use foundation::Confirmation;

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

/// What the references of one [`Connection`] show. A field is `None` unless its text is a `$NAME` reference that may
/// expand here; a variable that is not set expands to an empty text. A password is never shown, only whether it is set.
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

fn shown(
    names: Names,
    connection: Connection<'_>,
    also_to: Option<&str>,
    env: &Environment,
) -> (Shown, String) {
    let mut pending: Vec<Confirmation> = Vec::new();
    let expanded = Endpoint {
        host: connection.host,
        user: connection.user,
        password: connection.password,
        names,
        also_to,
    }
    .expand(env, &mut pending);
    // A reference may be shown when nothing it would be sent with waits for the user's say.
    let allowed = |text: &str| {
        reference(text).is_some_and(|name| pending.iter().all(|asked| asked.reference != name))
    };
    let preview = Shown {
        host: allowed(connection.host).then(|| expanded.host.clone()),
        user: allowed(connection.user).then(|| expanded.user.clone()),
        password_set: allowed(connection.password.expose()).then(|| !expanded.password.is_empty()),
    };
    (preview, expanded.host)
}

/// What `camera`'s and `broker`'s references show under `env`.
pub fn preview(
    env: &Environment,
    camera: Connection<'_>,
    broker: Option<Connection<'_>>,
) -> Previews {
    let (camera_shown, camera_host) = shown(CAMERA_NAMES, camera, None, env);
    // The camera relays the broker login, so the broker's references are sent to the camera too.
    let broker_shown = broker.map(|broker| {
        shown(
            BROKER_NAMES,
            broker,
            Some(camera_host.as_str()).filter(|host| !host.is_empty()),
            env,
        )
        .0
    });
    Previews {
        camera: camera_shown,
        broker: broker_shown.unwrap_or_default(),
    }
}

/// Whether the user's own configuration names the camera at `host` (the host `recipe_host` expands to): the host is
/// the reference bound to the camera's host role, or the user confirmed some reference for it.
pub fn vouches(env: &Environment, recipe_host: &str, host: &str) -> bool {
    reference(recipe_host) == Some(CAMERA_NAMES.host) || env.confirms_host(host)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(confirmed: &[(&str, &str)]) -> Environment {
        Environment::new(
            |name| match name {
                "AX_DEVIL_TARGET_ADDR" => Some("10.0.0.7".into()),
                "AX_DEVIL_TARGET_USER" => Some("root".into()),
                "AX_DEVIL_TARGET_PASS" => Some("s3cret".into()),
                "AX_DEVIL_MQTT_BROKER_USER" => Some("mqtt".into()),
                "AX_DEVIL_MQTT_BROKER_PASS" => Some("m-secret".into()),
                "OTHER" => Some("elsewhere".into()),
                _ => None,
            },
            confirmed.iter().map(|(reference, host)| Confirmation {
                reference: (*reference).into(),
                host: (*host).into(),
            }),
        )
    }

    fn connection<'a>(host: &'a str, user: &'a str, password: &'a Secret) -> Connection<'a> {
        Connection {
            host,
            user,
            password,
        }
    }

    #[test]
    fn the_bound_set_shows_its_values_but_a_password_only_as_set() {
        let password = Secret::new("$AX_DEVIL_TARGET_PASS");
        let shown = preview(
            &env(&[]),
            connection("$AX_DEVIL_TARGET_ADDR", "$AX_DEVIL_TARGET_USER", &password),
            None,
        )
        .camera;
        assert_eq!(shown.host.as_deref(), Some("10.0.0.7"));
        assert_eq!(shown.user.as_deref(), Some("root"));
        assert_eq!(shown.password_set, Some(true));
        assert!(!format!("{shown:?}").contains("s3cret"));
    }

    #[test]
    fn a_reference_that_would_go_to_a_host_nobody_confirmed_shows_nothing_until_confirmed() {
        let password = Secret::new("$AX_DEVIL_TARGET_PASS");
        // The user typed another host: the bound login would be sent to it.
        let typed = connection("192.168.1.9", "$AX_DEVIL_TARGET_USER", &password);
        assert_eq!(preview(&env(&[]), typed, None).camera, Shown::default());
        // A reference of another role, even for the right host.
        let none = Secret::default();
        let other = connection("$OTHER", "", &none);
        assert_eq!(preview(&env(&[]), other, None).camera.host, None);
        let confirmed = env(&[("OTHER", "elsewhere")]);
        assert_eq!(
            preview(&confirmed, other, None).camera.host.as_deref(),
            Some("elsewhere")
        );
    }

    #[test]
    fn the_broker_login_also_goes_to_the_camera() {
        let none = Secret::default();
        let password = Secret::new("$AX_DEVIL_MQTT_BROKER_PASS");
        let camera = connection("$AX_DEVIL_TARGET_ADDR", "", &none);
        let broker = connection("broker.local", "$AX_DEVIL_MQTT_BROKER_USER", &password);
        let previews = preview(&env(&[]), camera, Some(broker));
        assert_eq!(previews.camera.host.as_deref(), Some("10.0.0.7"));
        assert_eq!(previews.broker, Shown::default());
    }

    #[test]
    fn a_camera_is_vouched_for_by_its_binding_or_a_confirmation() {
        assert!(vouches(&env(&[]), "$AX_DEVIL_TARGET_ADDR", "10.0.0.7"));
        assert!(!vouches(&env(&[]), "10.0.0.7", "10.0.0.7"));
        assert!(vouches(&env(&[("X", "10.0.0.7")]), "10.0.0.7", "10.0.0.7"));
    }
}
