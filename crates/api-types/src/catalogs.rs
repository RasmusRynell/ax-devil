//! Wire types for the render catalogs the app offers.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// One catalog the picker offers.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct CatalogInfoWire {
    /// The id, such as `built-in:standard`.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
}
