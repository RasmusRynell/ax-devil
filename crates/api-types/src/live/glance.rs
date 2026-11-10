//! What the sidebar asks the backend about a stream's camera beyond the scan: whether the login saved with a stream
//! gets in, and what the references in the Add Live Stream form show. No password is in any of these.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The body of `POST /api/live/check`: the stream whose own login is checked. The answer is the camera as a scan would
/// report it (`NetworkRow`), or no content when nothing is known: the stream is not resolved, the user has not started
/// or confirmed its host, its host is not an IPv4 address, it has no user, or no Axis camera answered.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveCheckRequest {
    /// The workspace item.
    pub item: String,
}

/// The body of `POST /api/live/expansions`: the host texts of the Add Live Stream form as typed (blank takes the
/// configured default, whose references are the ones answered).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct ExpansionsRequest {
    /// The camera host.
    pub host: Option<String>,
    /// The MQTT broker host.
    pub broker_host: Option<String>,
}

/// What the references of the default connection texts show. A field is `null` unless its default is a `$NAME`
/// reference; a variable that is not set is an empty text, and a password only says whether it is set.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LiveExpansions {
    /// The camera host: the typed one when it is a reference, else the default.
    pub host: Option<String>,
    /// The default camera user.
    pub username: Option<String>,
    /// Whether the default camera password is set.
    pub password_set: Option<bool>,
    /// The MQTT broker host: the typed one when it is a reference, else the default.
    pub broker_host: Option<String>,
    /// The default broker user.
    pub broker_user: Option<String>,
    /// Whether the default broker password is set.
    pub broker_pass_set: Option<bool>,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<LiveCheckRequest>();
    types.visit::<ExpansionsRequest>();
    types.visit::<LiveExpansions>();
}
