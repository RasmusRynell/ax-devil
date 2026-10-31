//! The one place that turns what the user chose into workspace items. Dialogs, the command line and drops all
//! come through here, so they accept and refuse the same input.

use crate::item::{ItemId, Recipe, VideoRecipe, WorkspaceItem, file_name};
use foundation::UserError;
use std::path::Path;

/// Builds the item for the video file at `path`. The path is made absolute, so the item means the same file
/// whatever the working folder; whether the file exists is for resolving to say.
pub fn video(path: &Path) -> Result<WorkspaceItem, UserError> {
    let location = path.display().to_string();
    let absolute = std::path::absolute(path)
        .map_err(|_| UserError::new("Enter the path of a video file", location.clone()))?;
    let label = file_name(&absolute);
    if label.is_empty() {
        return Err(UserError::new("The path does not name a file", location));
    }
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label,
        recipe: Recipe::Video(VideoRecipe { video: absolute }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Content;
    use std::fs;

    #[test]
    fn a_video_path_becomes_an_item_labelled_with_the_file_name_whatever_the_working_folder() {
        let item = video(Path::new("recordings/gate.mp4")).unwrap();
        assert_eq!(item.label, "gate.mp4");
        let Recipe::Video(recipe) = &item.recipe;
        assert!(recipe.video.is_absolute());
        assert!(recipe.video.ends_with("recordings/gate.mp4"));
        assert_ne!(item.id, video(Path::new("recordings/gate.mp4")).unwrap().id);
    }

    #[test]
    fn input_that_names_no_file_is_refused_with_the_path_it_was_given() {
        for path in ["", "..", "recordings/.."] {
            let error = video(Path::new(path)).unwrap_err();
            assert_eq!(error.location, path, "{path:?}");
        }
    }

    #[test]
    fn resolving_checks_only_that_the_path_is_a_readable_regular_file() {
        let folder = tempfile::tempdir().unwrap();
        let not_a_video = folder.path().join("notes.mp4");
        fs::write(&not_a_video, "plain text").unwrap();

        let item = video(&not_a_video).unwrap();
        let Content::SeekableVideo(content) = item.recipe.resolve(&item.id).unwrap();
        assert_eq!(content.id.as_str(), item.id.to_string());
        assert_eq!(content.item_id, item.id);
        assert_eq!(content.display_name, "notes.mp4");
        assert_eq!(content.source.path, not_a_video);

        for unreadable in [folder.path().join("missing.mp4"), folder.path().to_owned()] {
            let item = video(&unreadable).unwrap();
            let error = item.recipe.resolve(&item.id).unwrap_err();
            assert_eq!(error.location, unreadable.display().to_string());
            assert!(!error.reason.is_empty());
        }
    }
}
