//! The catalogs shipped with the app, embedded from `assets/catalogs`.

/// A catalog shipped with the app.
pub(crate) struct BuiltIn {
    /// The name in `built-in:<name>`.
    pub(crate) name: &'static str,
    /// The catalog document.
    pub(crate) source: &'static str,
}

/// Every built-in catalog, Standard first: the first is the default and the order is the picker's.
pub(crate) static BUILT_INS: [BuiltIn; 6] = [
    BuiltIn {
        name: "standard",
        source: include_str!("../../../assets/catalogs/standard.json"),
    },
    BuiltIn {
        name: "tracking",
        source: include_str!("../../../assets/catalogs/tracking.json"),
    },
    BuiltIn {
        name: "classic",
        source: include_str!("../../../assets/catalogs/classic.json"),
    },
    BuiltIn {
        name: "chunky",
        source: include_str!("../../../assets/catalogs/chunky.json"),
    },
    BuiltIn {
        name: "glass",
        source: include_str!("../../../assets/catalogs/glass.json"),
    },
    BuiltIn {
        name: "minimal",
        source: include_str!("../../../assets/catalogs/minimal.json"),
    },
];
