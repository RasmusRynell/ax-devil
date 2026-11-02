use super::*;
use crate::{MAX_CATALOG_BYTES, MAX_USER_CATALOGS};
use std::{fs, path::Path};

fn served(library: &Library, file: &str) -> Served {
    library.document(&CatalogId::User(file.into()))
}

/// A catalog document of `name`, padded so that two versions of one file differ in size.
fn document(name: &str, padding: usize) -> String {
    format!(
        r#"{{"schema":4,"name":"{name}","description":"About {name}","layers":[]}}{}"#,
        " ".repeat(padding)
    )
}

fn revision(served: &Served) -> u32 {
    served.revision()
}

fn user_ids(library: &Library) -> Vec<String> {
    library
        .documents()
        .into_iter()
        .filter(|info| matches!(info.id, CatalogId::User(_)))
        .map(|info| info.id.to_string())
        .collect()
}

fn entries(folder: &Path) -> Vec<(String, Vec<u8>)> {
    let mut found: Vec<_> = fs::read_dir(folder)
        .unwrap()
        .map(|entry| {
            let entry = entry.unwrap();
            (
                entry.file_name().into_string().unwrap(),
                fs::read(entry.path()).unwrap_or_default(),
            )
        })
        .collect();
    found.sort();
    found
}

#[test]
fn the_built_ins_are_listed_by_their_own_names_and_served_at_revision_one() {
    let library = Library::built_in();

    let listed = library.documents();

    let ids: Vec<String> = listed.iter().map(|info| info.id.to_string()).collect();
    assert_eq!(
        ids,
        [
            "built-in:standard",
            "built-in:tracking",
            "built-in:classic",
            "built-in:chunky",
            "built-in:glass",
            "built-in:minimal"
        ]
    );
    assert_eq!(listed[0].name, "Standard");
    assert_eq!(library.default_id(), listed[0].id);
    for info in &listed {
        assert!(!info.description.is_empty());
        let Served::Document { revision, text } = library.document(&info.id) else {
            panic!("a listed id is served");
        };
        assert_eq!(revision, 1);
        assert!(text.contains("\"schema\""));
    }
    for unserved in [
        CatalogId::BuiltIn("nope"),
        CatalogId::User("night.json".into()),
    ] {
        assert_eq!(library.document(&unserved), Served::Missing { revision: 0 });
    }
    assert!(!library.rescan(), "no folder, nothing to poll");
}

#[test]
fn the_folder_lists_its_json_files_after_the_built_ins_and_serves_what_each_holds_without_writing()
{
    let folder = tempfile::tempdir().unwrap();
    let root = folder.path();
    fs::write(root.join("night.json"), document("Night", 0)).unwrap();
    fs::write(root.join("plain.json"), "{}").unwrap();
    fs::write(root.join("broken.json"), "{ not json").unwrap();
    fs::write(root.join("notes.txt"), "not a catalog").unwrap();
    fs::create_dir(root.join("folder.json")).unwrap();
    fs::write(
        root.join("big.json"),
        vec![b' '; MAX_CATALOG_BYTES as usize + 1],
    )
    .unwrap();
    fs::write(root.join("binary.json"), [0xff, 0xfe, 0x00]).unwrap();
    let before = entries(root);

    let library = Library::with_folder(root.to_path_buf());

    assert_eq!(
        user_ids(&library),
        [
            "user:big.json",
            "user:binary.json",
            "user:broken.json",
            "user:night.json",
            "user:plain.json"
        ]
    );
    let listed = library.documents();
    assert_eq!(listed[0].id.to_string(), "built-in:standard");
    let info = |file: &str| {
        listed
            .iter()
            .find(|info| info.id.to_string() == format!("user:{file}"))
            .unwrap()
            .clone()
    };
    assert_eq!(
        (info("night.json").name, info("night.json").description),
        ("Night".to_owned(), "About Night".to_owned())
    );
    // A document that does not say its name is named by its file; the compiler tells the user why it is no catalog.
    assert_eq!(info("plain.json").name, "plain.json");
    assert_eq!(info("broken.json").name, "broken.json");
    assert!(info("broken.json").description.starts_with("Not a catalog"));
    assert!(matches!(
        served(&library, "night.json"),
        Served::Document { .. }
    ));
    assert!(matches!(
        served(&library, "broken.json"),
        Served::Document { .. }
    ));
    for unreadable in ["big.json", "binary.json"] {
        let Served::Unreadable { problem, .. } = served(&library, unreadable) else {
            panic!("{unreadable} cannot be served");
        };
        assert!(!problem.contains(unreadable), "{problem}");
        assert_eq!(info(unreadable).description, problem);
    }
    assert_eq!(
        served(&library, "notes.txt"),
        Served::Missing { revision: 0 }
    );
    assert_eq!(entries(root), before, "the folder is never written");
}

#[test]
fn a_changed_file_is_served_only_after_it_stayed_the_same_for_a_poll_and_revisions_never_go_down() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("night.json");
    fs::write(&file, document("Night", 0)).unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let mut changes = library.changes();
    let first = revision(&served(&library, "night.json"));
    assert!(first > 1);

    // The same bytes, touched, are no change.
    fs::write(&file, document("Night", 0)).unwrap();
    assert!(!library.rescan());
    assert!(!library.rescan());
    assert_eq!(revision(&served(&library, "night.json")), first);
    assert!(!changes.has_changed().unwrap());

    // A save that is still going on: each poll sees another size, and the old document stays served.
    fs::write(&file, document("Dusk", 1)).unwrap();
    assert!(!library.rescan());
    fs::write(&file, document("Dusk", 2)).unwrap();
    assert!(!library.rescan());
    assert_eq!(library.documents()[6].name, "Night");
    assert!(matches!(served(&library, "night.json"),
        Served::Document { text, .. } if text.contains("Night")));
    assert!(!changes.has_changed().unwrap());

    // It stayed as it was for one poll: now it is read.
    assert!(library.rescan());
    let second = revision(&served(&library, "night.json"));
    assert!(second > first);
    assert_eq!(library.documents()[6].name, "Dusk");
    assert!(changes.has_changed().unwrap());
    changes.mark_unchanged();

    // A file that appears is listed after it stayed as it was for one poll.
    fs::write(folder.path().join("dawn.json"), document("Dawn", 0)).unwrap();
    assert!(!library.rescan());
    assert_eq!(user_ids(&library), ["user:night.json"]);
    assert!(library.rescan());
    assert_eq!(user_ids(&library), ["user:dawn.json", "user:night.json"]);
    assert!(revision(&served(&library, "dawn.json")) > second);
    assert!(changes.has_changed().unwrap());

    // A file that is missing for one poll, as while an editor swaps it, is still served.
    fs::remove_file(&file).unwrap();
    assert!(!library.rescan());
    assert!(matches!(
        served(&library, "night.json"),
        Served::Document { .. }
    ));
    fs::write(&file, document("Night again", 3)).unwrap();
    assert!(!library.rescan());
    assert!(library.rescan());
    let swapped = revision(&served(&library, "night.json"));
    assert!(swapped > second);

    // One that stays missing is gone, at a revision above any it had, and a file of the same name starts above that.
    fs::remove_file(&file).unwrap();
    assert!(!library.rescan());
    assert!(library.rescan());
    let Served::Missing { revision: deleted } = served(&library, "night.json") else {
        panic!("a deleted file is missing");
    };
    assert!(deleted > swapped);
    assert_eq!(user_ids(&library), ["user:dawn.json"]);
    fs::write(&file, document("Night", 0)).unwrap();
    assert!(!library.rescan());
    assert!(library.rescan());
    assert!(revision(&served(&library, "night.json")) > deleted);

    // Another library over the same folder, as after a restart of the poller, starts above every revision given.
    let later = Library::with_folder(folder.path().to_path_buf());
    assert!(revision(&served(&later, "dawn.json")) > revision(&served(&library, "dawn.json")));
}

#[test]
fn only_the_first_256_files_by_name_are_listed() {
    let folder = tempfile::tempdir().unwrap();
    for number in 0..=MAX_USER_CATALOGS {
        fs::write(folder.path().join(format!("{number:03}.json")), "{}").unwrap();
    }

    let library = Library::with_folder(folder.path().to_path_buf());

    let ids = user_ids(&library);
    assert_eq!(ids.len(), MAX_USER_CATALOGS);
    assert_eq!(ids.last().unwrap(), "user:255.json");
    assert_eq!(
        served(&library, "256.json"),
        Served::Missing { revision: 0 }
    );
}

#[test]
fn a_folder_that_does_not_exist_yet_lists_nothing_and_fills_when_it_appears() {
    let parent = tempfile::tempdir().unwrap();
    let folder = parent.path().join("catalogs");
    let library = Library::with_folder(folder.clone());
    assert!(user_ids(&library).is_empty());
    assert!(!library.rescan());

    fs::create_dir(&folder).unwrap();
    fs::write(folder.join("night.json"), document("Night", 0)).unwrap();
    assert!(!library.rescan());
    assert!(library.rescan());

    assert_eq!(user_ids(&library), ["user:night.json"]);
}
