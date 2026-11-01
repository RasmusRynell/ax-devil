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

/// The v4 documents the playback socket serves, by name: the picker offers these until the v3 catalogs are retired.
pub(crate) static DOCUMENTS: [(&str, &str); 3] = [
    (
        "standard",
        include_str!("../../../assets/catalogs/standard.v4.json"),
    ),
    (
        "classic",
        include_str!("../../../assets/catalogs/classic.v4.json"),
    ),
    (
        "glass",
        include_str!("../../../assets/catalogs/glass.v4.json"),
    ),
];
