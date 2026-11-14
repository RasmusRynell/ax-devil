//! The catalogs shipped with the app, embedded from `assets/catalogs`.

/// Every built-in catalog document by name, Standard first: the first is the default and the order is the picker's.
pub(crate) static DOCUMENTS: [(&str, &str); 6] = [
    (
        "standard",
        include_str!("../../../assets/catalogs/standard.v4.json"),
    ),
    (
        "minimal",
        include_str!("../../../assets/catalogs/minimal.v4.json"),
    ),
    (
        "chunky",
        include_str!("../../../assets/catalogs/chunky.v4.json"),
    ),
    (
        "glass",
        include_str!("../../../assets/catalogs/glass.v4.json"),
    ),
    (
        "tracking",
        include_str!("../../../assets/catalogs/tracking.v4.json"),
    ),
    (
        "classic",
        include_str!("../../../assets/catalogs/classic.v4.json"),
    ),
];
