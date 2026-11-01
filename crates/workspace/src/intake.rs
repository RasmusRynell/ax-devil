//! The one place that turns what the user chose into workspace items. Dialogs, the command line and drops all
//! come through here, so they accept and refuse the same input.

use crate::item::{
    ItemId, OverlayRecipe, PlaylistRecipe, Recipe, VideoRecipe, WorkspaceItem, file_name,
};
use foundation::UserError;
use handlers::Handlers;
use playlist_resolvers::{FieldKind, Settings};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// An overlay file the user chose to add to a video.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NewOverlay {
    /// The overlay file.
    pub path: PathBuf,
    /// The decoder handler id; `None` picks the only decoder for the file's extension.
    pub handler: Option<String>,
}

/// Builds the item for the video file at `path`, with `overlays` drawn on it. The paths are made absolute, so the
/// item means the same files whatever the working folder; whether they exist is for resolving to say.
///
/// An overlay without a handler gets the only decoder for its extension; the error names the candidates when
/// several read it, and the file when none does. A named handler must be one the app knows.
pub fn video(
    path: &Path,
    overlays: &[NewOverlay],
    handlers: &Handlers,
) -> Result<WorkspaceItem, UserError> {
    let location = path.display().to_string();
    let absolute = std::path::absolute(path)
        .map_err(|_| UserError::new("Enter the path of a video file", location.clone()))?;
    let label = file_name(&absolute);
    if label.is_empty() {
        return Err(UserError::new("The path does not name a file", location));
    }
    let overlays = overlays
        .iter()
        .map(|overlay| overlay_recipe(overlay, handlers))
        .collect::<Result<_, _>>()?;
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label,
        recipe: Recipe::Video(VideoRecipe {
            video: absolute,
            overlays,
        }),
    })
}

fn overlay_recipe(overlay: &NewOverlay, handlers: &Handlers) -> Result<OverlayRecipe, UserError> {
    let location = overlay.path.display().to_string();
    let path = std::path::absolute(&overlay.path)
        .ok()
        .filter(|path| !file_name(path).is_empty())
        .ok_or_else(|| UserError::new("The path does not name a file", location.clone()))?;
    let handler = match &overlay.handler {
        Some(handler) if handlers.file_decoder(handler).is_some() => handler.clone(),
        Some(handler) => {
            return Err(UserError::new(
                format!("No overlay format \"{handler}\""),
                location,
            ));
        }
        None => {
            let candidates: Vec<&str> = handlers
                .file_decoders_for(&path)
                .iter()
                .map(|decoder| decoder.spec().handler)
                .collect();
            match candidates[..] {
                [only] => only.to_owned(),
                [] => {
                    return Err(UserError::new(
                        "No overlay format reads this kind of file",
                        location,
                    ));
                }
                _ => {
                    return Err(UserError::new(
                        format!(
                            "Several overlay formats read this file; choose one of {}",
                            candidates.join(", ")
                        ),
                        location,
                    ));
                }
            }
        }
    };
    Ok(OverlayRecipe { path, handler })
}

/// Builds the item for a playlist of `resolver` with `settings`, labelled `label` or with the resolver's name. The
/// settings are checked against the resolver's schema, and the paths in them made absolute; reading the folders is
/// for resolving to do.
pub fn playlist(
    resolver: &str,
    mut settings: Settings,
    label: Option<String>,
    handlers: &Handlers,
) -> Result<WorkspaceItem, UserError> {
    let Some(found) = handlers.resolver(resolver) else {
        return Err(UserError::new("No such playlist resolver", resolver));
    };
    let schema = found.schema();
    schema.validate(&settings)?;
    for field in schema.fields {
        if !matches!(field.kind, FieldKind::Folder | FieldKind::File { .. }) {
            continue;
        }
        if let Some(Value::String(path)) = settings.get_mut(field.key)
            && let Ok(absolute) = std::path::absolute(&*path)
        {
            *path = absolute.to_string_lossy().into_owned();
        }
    }
    let label = label
        .map(|label| label.trim().to_owned())
        .filter(|label| !label.is_empty())
        .unwrap_or_else(|| found.spec().display_name.to_owned());
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label,
        recipe: Recipe::Playlist(PlaylistRecipe {
            resolver: resolver.to_owned(),
            settings,
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Content, VideoRef};
    use foundation::{CancelToken, Interrupt};
    use std::fs;
    use std::path::PathBuf;

    #[test]
    fn a_video_path_becomes_an_item_labelled_with_the_file_name_whatever_the_working_folder() {
        let item = video(Path::new("recordings/gate.mp4"), &[], &Handlers::built_in()).unwrap();
        assert_eq!(item.label, "gate.mp4");
        let Recipe::Video(recipe) = &item.recipe else {
            panic!("a video item has a video recipe");
        };
        assert!(recipe.video.is_absolute());
        assert!(recipe.video.ends_with("recordings/gate.mp4"));
        let again = video(Path::new("recordings/gate.mp4"), &[], &Handlers::built_in()).unwrap();
        assert_ne!(item.id, again.id);
    }

    #[test]
    fn input_that_names_no_file_is_refused_with_the_path_it_was_given() {
        for path in ["", "..", "recordings/.."] {
            let error = video(Path::new(path), &[], &Handlers::built_in()).unwrap_err();
            assert_eq!(error.location, path, "{path:?}");
        }
    }

    #[test]
    fn resolving_checks_only_that_the_path_is_a_readable_regular_file() {
        let folder = tempfile::tempdir().unwrap();
        let not_a_video = folder.path().join("notes.mp4");
        fs::write(&not_a_video, "plain text").unwrap();

        let handlers = Handlers::built_in();
        let item = video(&not_a_video, &[], &handlers).unwrap();
        let Content::SeekableVideo(content) = item
            .recipe
            .resolve(&item.id, &handlers, &CancelToken::new())
            .unwrap()
        else {
            panic!("a video recipe resolves to a video");
        };
        assert_eq!(content.entry.id.as_str(), item.id.to_string());
        assert_eq!(content.item_id, item.id);
        assert_eq!(content.entry.label, "notes.mp4");
        assert_eq!(content.entry.video, VideoRef::File(not_a_video));
        assert_eq!(content.entry.lane_labels(), ["notes.mp4"]);

        for unreadable in [folder.path().join("missing.mp4"), folder.path().to_owned()] {
            let item = video(&unreadable, &[], &handlers).unwrap();
            let Err(Interrupt::Failed(error)) =
                item.recipe
                    .resolve(&item.id, &handlers, &CancelToken::new())
            else {
                panic!("an unreadable path fails to resolve");
            };
            assert_eq!(error.location, unreadable.display().to_string());
            assert!(!error.reason.is_empty());
        }
    }

    fn overlay(path: &Path, handler: Option<&str>) -> NewOverlay {
        NewOverlay {
            path: path.to_owned(),
            handler: handler.map(str::to_owned),
        }
    }

    fn overlay_handlers(item: &WorkspaceItem) -> Vec<&str> {
        let Recipe::Video(recipe) = &item.recipe else {
            panic!("a video item has a video recipe");
        };
        recipe.overlays.iter().map(|o| o.handler.as_str()).collect()
    }

    #[test]
    fn an_overlay_gets_the_only_decoder_for_its_extension_or_is_refused_naming_why() {
        let handlers = Handlers::built_in();
        let add = |path: &str, handler: Option<&str>| {
            video(
                Path::new("gate.mp4"),
                &[overlay(Path::new(path), handler)],
                &handlers,
            )
        };

        assert_eq!(
            overlay_handlers(&add("gt/GT.TXT", None).unwrap()),
            ["MOT_FILE"]
        );
        assert_eq!(
            overlay_handlers(&add("boxes.xml", Some("MOT_FILE")).unwrap()),
            ["MOT_FILE"],
            "a named decoder wins over the extension"
        );

        let several = add("boxes.xml", None).unwrap_err();
        assert_eq!(several.location, "boxes.xml");
        for candidate in handlers.file_decoders_for(Path::new("boxes.xml")) {
            assert!(
                several.reason.contains(candidate.spec().handler),
                "{}",
                several.reason
            );
        }
        assert_eq!(add("notes.mp3", None).unwrap_err().location, "notes.mp3");
        assert_eq!(
            add("gt.txt", Some("NOPE")).unwrap_err().reason,
            "No overlay format \"NOPE\""
        );
    }

    #[test]
    fn a_video_with_overlays_resolves_to_one_lane_per_overlay_and_checks_each_file() {
        let folder = tempfile::tempdir().unwrap();
        let (video_file, gt, det) = (
            folder.path().join("gate.mp4"),
            folder.path().join("gt.txt"),
            folder.path().join("det.txt"),
        );
        for file in [&video_file, &gt, &det] {
            fs::write(file, "x").unwrap();
        }
        let handlers = Handlers::built_in();
        let resolve = |overlays: &[NewOverlay]| {
            let item = video(&video_file, overlays, &handlers).unwrap();
            item.recipe
                .resolve(&item.id, &handlers, &CancelToken::new())
        };

        let Ok(Content::SeekableVideo(content)) =
            resolve(&[overlay(&gt, None), overlay(&det, None)])
        else {
            panic!("readable files resolve");
        };
        assert_eq!(content.entry.lane_labels(), ["gt.txt", "det.txt"]);
        assert_eq!(content.entry.overlays[0].handler, "MOT_FILE");

        let Err(Interrupt::Failed(missing)) =
            resolve(&[overlay(&folder.path().join("none.txt"), None)])
        else {
            panic!("a missing overlay fails");
        };
        assert!(missing.location.ends_with("none.txt"));

        let other = folder.path().join("other");
        fs::create_dir(&other).unwrap();
        fs::write(other.join("gt.txt"), "x").unwrap();
        let Err(Interrupt::Failed(twice)) =
            resolve(&[overlay(&gt, None), overlay(&other.join("gt.txt"), None)])
        else {
            panic!("two overlays with one file name fail");
        };
        assert_eq!(twice.location, "gt.txt");
    }

    fn mot_folder(sequences: &[&str]) -> tempfile::TempDir {
        let folder = tempfile::tempdir().unwrap();
        for name in sequences {
            let dir = folder.path().join(name);
            fs::create_dir_all(dir.join("gt")).unwrap();
            fs::write(dir.join("gt/gt.txt"), "1,1,0,0,1,1,1,-1,-1,-1\n").unwrap();
            fs::write(
                dir.join("seqinfo.ini"),
                "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
            )
            .unwrap();
        }
        folder
    }

    fn mot_settings(folder: &Path) -> Settings {
        let mut settings = Settings::new();
        settings.insert("root".into(), folder.display().to_string().into());
        settings
    }

    #[test]
    fn a_playlist_resolves_to_entries_whose_ids_are_the_same_every_time() {
        let folder = mot_folder(&["SEQ-02", "SEQ-01"]);
        let handlers = Handlers::built_in();
        let item = playlist(
            "mot_challenge",
            mot_settings(folder.path()),
            None,
            &handlers,
        )
        .unwrap();
        assert_eq!(item.label, "MOT Challenge");
        let resolve = || {
            let Ok(Content::Playlist(content)) =
                item.recipe
                    .resolve(&item.id, &handlers, &CancelToken::new())
            else {
                panic!("a playlist recipe resolves to a playlist");
            };
            content
        };

        let (first, second) = (resolve(), resolve());

        assert_eq!(first, second);
        assert_eq!(first.id.as_str(), item.id.to_string());
        let ids: Vec<String> = first.entries.iter().map(|e| e.id.to_string()).collect();
        assert_eq!(
            ids,
            [format!("{}/SEQ-01", item.id), format!("{}/SEQ-02", item.id)]
        );
        assert_eq!(first.entries[0].lane_labels(), ["GT"]);
        assert!(
            Content::Playlist(first)
                .entry(&ids[1])
                .is_some_and(|entry| entry.label == "SEQ-02")
        );
    }

    #[test]
    fn a_playlist_the_schema_or_resolver_refuses_is_refused_naming_the_setting() {
        let handlers = Handlers::built_in();
        let none = playlist("mot_challenge", Settings::new(), None, &handlers).unwrap_err();
        assert_eq!(none.location, "root");
        assert_eq!(
            playlist("nope", Settings::new(), None, &handlers)
                .unwrap_err()
                .location,
            "nope"
        );

        let gone = playlist(
            "mot_challenge",
            mot_settings(Path::new("no/such/folder")),
            Some(" Mine ".into()),
            &handlers,
        )
        .unwrap();
        assert_eq!(gone.label, "Mine");
        let Recipe::Playlist(recipe) = &gone.recipe else {
            panic!("a playlist item has a playlist recipe");
        };
        assert!(Path::new(recipe.settings["root"].as_str().unwrap()).is_absolute());
        let Err(Interrupt::Failed(error)) =
            gone.recipe
                .resolve(&gone.id, &handlers, &CancelToken::new())
        else {
            panic!("a missing folder fails to resolve");
        };
        assert!(error.location.ends_with("no/such/folder"), "{error:?}");
    }

    fn resolved_entry(name: &str, labels: &[&str]) -> playlist_resolvers::ResolvedEntry {
        playlist_resolvers::ResolvedEntry {
            name: name.to_owned(),
            video: VideoRef::File(PathBuf::from("v.mp4")),
            overlays: labels
                .iter()
                .map(|label| playlist_resolvers::OverlayRef {
                    label: (*label).to_owned(),
                    path: PathBuf::from("o.txt"),
                    handler: "MOT_FILE".into(),
                    options: Settings::new(),
                })
                .collect(),
        }
    }

    #[test]
    fn duplicate_entry_names_and_duplicate_lane_labels_fail_naming_the_label() {
        let item = ItemId::generate();
        let build = |entries| {
            crate::content::playlist_content(
                &item,
                playlist_resolvers::ResolvedPlaylist {
                    display_name: "P".into(),
                    entries,
                },
            )
        };

        let entries = build(vec![resolved_entry("A", &["GT"]), resolved_entry("A", &[])]);
        assert_eq!(entries.unwrap_err().location, "A");
        let lanes = build(vec![resolved_entry("A", &["GT", "DET", "GT"])]);
        assert_eq!(lanes.unwrap_err().location, "GT");
        assert!(
            build(vec![
                resolved_entry("A", &["GT"]),
                resolved_entry("B", &["GT"])
            ])
            .is_ok()
        );
    }
}
