//! Resolving a video recipe or a playlist: which file is checked first, how lanes are named, and that a cancelled
//! attempt publishes nothing.

use super::tests::{mot_folder, mot_settings, overlay};
use super::*;
use crate::{Content, Environment};
use foundation::{CancelToken, Interrupt};
use handler_api::Values;
use std::fs;
use std::sync::Arc;

#[test]
fn overlays_in_different_folders_with_one_file_name_are_lanes_named_by_stem_and_number() {
    let folder = tempfile::tempdir().unwrap();
    let video_file = folder.path().join("gate.mp4");
    let (day, night) = (folder.path().join("day"), folder.path().join("night"));
    fs::write(&video_file, "x").unwrap();
    for dir in [&day, &night] {
        fs::create_dir(dir).unwrap();
        fs::write(dir.join("gt.txt"), "x").unwrap();
    }
    let handlers = Handlers::built_in();
    let overlays = [
        overlay(&day.join("gt.txt"), None),
        overlay(&night.join("gt.txt"), None),
        overlay(&day.join("gt.txt"), None),
    ];
    let item = video(&video_file, &overlays, &handlers).unwrap();
    let Ok(Content::SeekableVideo(content)) =
        item.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
    else {
        panic!("distinct files with one name resolve");
    };
    assert_eq!(content.entry.lane_labels(), ["gt", "gt (2)", "gt (3)"]);
    assert_eq!(
        content.entry.overlays[1].file(),
        Some(night.join("gt.txt").as_path())
    );
}

#[test]
fn every_file_is_checked_before_any_decoder_is_looked_up() {
    let folder = tempfile::tempdir().unwrap();
    let (video_file, present) = (folder.path().join("gate.mp4"), folder.path().join("gt.txt"));
    let missing = folder.path().join("missing.txt");
    for file in [&video_file, &present] {
        fs::write(file, "x").unwrap();
    }
    let handlers = Handlers::built_in();
    let failure = |overlays: &[(&Path, &str)]| {
        let item = WorkspaceItem {
            id: ItemId::generate(),
            label: String::new(),
            recipe: Recipe::new(Source::Video(VideoRecipe {
                video: HostPath::new(&video_file),
                overlays: overlays
                    .iter()
                    .map(|(path, handler)| OverlayRecipe {
                        path: HostPath::new(path),
                        handler: (*handler).to_owned(),
                        label: file_name(path),
                        options: Settings::new(),
                    })
                    .collect(),
            })),
        };
        match item.resolve(&handlers, &Environment::of_process(), &CancelToken::new()) {
            Err(Interrupt::Failed(error)) => error,
            _ => panic!("the recipe should fail to resolve"),
        }
    };

    let error = failure(&[(&present, "NOPE"), (&missing, "NOPE")]);
    assert_eq!(error.code, ErrorCode::FileNotFound);
    assert_eq!(error.location, Some(HostPath::new(&missing).into()));

    let error = failure(&[(&present, "NOPE")]);
    assert_eq!(error.code, ErrorCode::UnknownHandler);
    assert_eq!(error.location, Some(HostPath::new(&present).into()));
}

/// A resolver that runs the folder resolver of MOT Challenge, then cancels the token, as a newer attempt would.
struct CancelsAfterResolving(handler_api::ResolverSpec);

impl handler_api::PlaylistResolver for CancelsAfterResolving {
    fn spec(&self) -> &handler_api::ResolverSpec {
        &self.0
    }

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<handler_api::ResolvedPlaylists, Interrupt<UserError>> {
        let resolved = Handlers::built_in()
            .resolver("mot_challenge")
            .unwrap()
            .resolve(settings, &CancelToken::new());
        cancel.cancel();
        resolved
    }
}

#[test]
fn an_attempt_cancelled_while_resolving_publishes_no_content() {
    let folder = mot_folder(&["SEQ-01"]);
    let spec = handler_api::ResolverSpec {
        id: "cancelling".into(),
        ..Handlers::built_in()
            .resolver("mot_challenge")
            .unwrap()
            .spec()
            .clone()
    };
    let handlers = Handlers::built_in().with_resolvers([
        Arc::new(CancelsAfterResolving(spec)) as Arc<dyn handler_api::PlaylistResolver>
    ]);
    let item = playlist("cancelling", mot_settings(folder.path()), &handlers).unwrap();
    let token = CancelToken::new();
    assert!(matches!(
        item.resolve(&handlers, &Environment::of_process(), &token),
        Err(Interrupt::Cancelled)
    ));
    assert!(token.is_cancelled());

    let item = video(Path::new("gate.mp4"), &[], &handlers).unwrap();
    let cancelled = CancelToken::new();
    cancelled.cancel();
    assert!(matches!(
        item.resolve(&handlers, &Environment::of_process(), &cancelled),
        Err(Interrupt::Cancelled)
    ));
}
