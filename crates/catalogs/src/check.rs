//! Checking a catalog file without using it.

use std::{fs::File, io::Read, path::Path};

use render_catalog::{CatalogDiagnostic, CatalogMetadata, codes};

/// The diagnostic code of a file that could not be read.
pub const UNREADABLE: &str = "unreadable_file";

/// The largest catalog the language accepts; a file past it is not read further.
const MAX_CATALOG_BYTES: u64 = 2_000_000;

/// Reads and validates the catalog in `path`, as the app would use it. Success gives what the catalog says about
/// itself; a catalog with problems gives every one of them at once, each with its JSON pointer, and a file that cannot
/// be read gives one problem with the reason.
pub fn check_file(path: &Path) -> Result<CatalogMetadata, Vec<CatalogDiagnostic>> {
    let source = read(path).map_err(|(code, message)| {
        vec![CatalogDiagnostic {
            code,
            location: String::new(),
            message,
        }]
    })?;
    render_catalog::compile(&source).map(|compiled| compiled.metadata().clone())
}

fn read(path: &Path) -> Result<String, (&'static str, String)> {
    let unreadable = |error: std::io::Error| {
        (
            UNREADABLE,
            format!("Cannot read {}: {error}", path.display()),
        )
    };
    let mut bytes = Vec::new();
    File::open(path)
        .and_then(|file| file.take(MAX_CATALOG_BYTES + 1).read_to_end(&mut bytes))
        .map_err(unreadable)?;
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return Err((
            codes::EXECUTION_LIMIT,
            format!(
                "The catalog is larger than {} MB",
                MAX_CATALOG_BYTES / 1_000_000
            ),
        ));
    }
    String::from_utf8(bytes)
        .map_err(|_| (codes::INVALID_JSON, "The file is not UTF-8 text".to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(folder: &tempfile::TempDir, name: &str, body: &[u8]) -> std::path::PathBuf {
        let path = folder.path().join(name);
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn a_good_catalog_reports_what_it_says_about_itself() {
        let folder = tempfile::tempdir().unwrap();
        let standard = include_str!("../../../assets/catalogs/standard.json");
        let path = write(&folder, "copy.json", standard.as_bytes());

        let metadata = check_file(&path).unwrap();

        assert_eq!(metadata.name, "Standard");
        assert_eq!(metadata.id, "built-in.standard");
    }

    #[test]
    fn a_bad_catalog_yields_every_problem_with_where_it_is() {
        let folder = tempfile::tempdir().unwrap();
        let standard = include_str!("../../../assets/catalogs/standard.json");
        let broken = standard.replace("\"schema_version\": 3", "\"schema_version\": 2");
        assert_ne!(broken, standard);

        let versions = check_file(&write(&folder, "old.json", broken.as_bytes())).unwrap_err();
        assert!(
            versions.iter().any(
                |d| d.code == codes::UNSUPPORTED_VERSION && d.location.starts_with("/metadata")
            ),
            "{versions:?}"
        );

        let truncated = check_file(&write(&folder, "cut.json", b"{ \"metadata\": ")).unwrap_err();
        assert_eq!(truncated[0].code, codes::INVALID_JSON);
        assert!(!truncated[0].message.is_empty());
    }

    #[test]
    fn a_file_that_cannot_be_read_is_one_problem_naming_it() {
        let folder = tempfile::tempdir().unwrap();
        let missing = folder.path().join("missing.json");
        let missing = check_file(&missing).unwrap_err();
        assert_eq!(missing.len(), 1);
        assert_eq!(missing[0].code, UNREADABLE);
        assert!(missing[0].message.contains("missing.json"));

        let binary = check_file(&write(&folder, "bytes.json", &[0xff, 0xfe, 0x00])).unwrap_err();
        assert_eq!(binary[0].code, codes::INVALID_JSON);

        let huge = vec![b' '; 2_000_001];
        let huge = check_file(&write(&folder, "huge.json", &huge)).unwrap_err();
        assert_eq!(huge[0].code, codes::EXECUTION_LIMIT);
    }
}
