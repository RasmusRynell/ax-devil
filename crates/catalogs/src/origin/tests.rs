use crate::{CatalogId, Library, Origin};
use serde_json::{Value, json};
use std::fs;

#[test]
fn a_copy_is_modified_when_its_body_changes_and_not_when_it_is_only_renamed_or_described() {
    let folder = tempfile::tempdir().unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let copy = library
        .copy(&CatalogId::BuiltIn("standard"), "Mine")
        .unwrap();
    let file = folder.path().join("mine.json");
    let origin = |library: &Library| {
        library
            .documents()
            .into_iter()
            .find(|info| info.id == copy)
            .and_then(|info| info.origin)
    };
    let edit = |library: &Library, change: &dyn Fn(&mut Value)| {
        let mut document: Value =
            serde_json::from_str(&fs::read_to_string(&file).unwrap()).unwrap();
        change(&mut document);
        fs::write(&file, serde_json::to_string(&document).unwrap()).unwrap();
        // A changed file is listed after it looked the same on two polls.
        library.rescan();
        library.rescan();
    };
    let standard = |modified| {
        Some(Origin {
            based_on: "Standard".into(),
            modified,
        })
    };

    assert_eq!(origin(&library), standard(false));
    assert_eq!(origin(&library).unwrap().note(), "copy of Standard");

    edit(&library, &|document| {
        document["name"] = json!("Renamed, and described at length");
        document["description"] = json!("Another description entirely.");
    });
    assert_eq!(origin(&library), standard(false), "text is not the body");

    edit(&library, &|document| document["layers"] = json!([]));
    assert_eq!(origin(&library), standard(true));
    assert_eq!(
        origin(&library).unwrap().note(),
        "copy of Standard, modified"
    );
}
