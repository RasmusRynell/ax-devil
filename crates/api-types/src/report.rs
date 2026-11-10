//! Wire type for the route that records a page's uncaught errors.

use serde::Deserialize;
use ts_rs::TS;

/// An error nobody caught in a page: the body of `POST /api/client-errors`. The backend adds who sent it.
#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PageError {
    /// `Name: message`.
    pub summary: String,
    /// The summary and the stack trace.
    pub details: String,
    /// The page's address without its query and fragment, which may hold credentials.
    pub page: String,
    /// Whether the page runs in the desktop app (and not in a browser).
    pub desktop: bool,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<PageError>();
}
