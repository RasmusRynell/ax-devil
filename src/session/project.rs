//! What an audience may learn of the Doc: the only constructor of [`DocWire`].
//!
//! The function starts with an exhaustive destructure of `Doc` (no `..`; `scripts/check-architecture.cjs` rejects a
//! rest pattern in this file), so a new Doc field does not compile until its projection is decided here. An audience
//! without host details sees only items the Doc has open, and for a failed item only its public code and the item id:
//! never a path, a recipe or the reason text. What a user wrote, such as labels and the workspace name, is shared on
//! purpose.

use api_types::{
    Audience, DocWire, project as project_error,
    workspace::{ContentInfo, ItemState, ResolutionState, WorkspaceState},
};
use sessions::session::Doc;
use workspace::{Content, SeekableVideoContent, Workspace, WorkspaceItem};
use workspace_store::{ContentGen, Resolution};

/// `doc` as `audience` may see it.
pub fn project(doc: &Doc, audience: Audience) -> DocWire {
    let Doc { workspace } = doc;
    let Workspace { name, items } = &workspace.workspace;
    let sees_everything = audience.host_details;
    let items = items
        .iter()
        .filter(|item| sees_everything || workspace.open.contains(&item.id))
        .map(|item| {
            let WorkspaceItem {
                id,
                label,
                recipe: _,
            } = item;
            ItemState {
                id: id.to_string(),
                label: label.clone(),
                open: workspace.open.contains(id),
                resolution: match workspace.resolutions.get(id) {
                    Some(Resolution::Resolved {
                        content,
                        generation,
                    }) => ResolutionState::Resolved {
                        content: content_info(content, *generation),
                    },
                    Some(Resolution::Failed(error)) => {
                        ResolutionState::Failed(project_error(error, audience))
                    }
                    Some(Resolution::Pending { attempt: _ }) | None => ResolutionState::Pending,
                },
            }
        })
        .collect();
    DocWire {
        workspace: WorkspaceState {
            name: name.clone(),
            items,
        },
    }
}

fn content_info(content: &Content, generation: ContentGen) -> ContentInfo {
    match content {
        Content::SeekableVideo(video) => {
            let SeekableVideoContent {
                id,
                item_id: _,
                display_name,
                source: _,
            } = video;
            ContentInfo::SeekableVideo {
                id: id.to_string(),
                generation: generation.0,
                display_name: display_name.clone(),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::{ErrorCode, HostPath, ItemId, UserError};
    use workspace::{ContentId, FileVideoSource, Recipe, VideoRecipe};
    use workspace_store::WorkspaceState as State;

    fn item(id: ItemId, label: &str, folder: &str) -> WorkspaceItem {
        WorkspaceItem {
            id,
            label: label.into(),
            recipe: Recipe::Video(VideoRecipe {
                video: format!("/home/{folder}/{label}").into(),
            }),
        }
    }

    /// A Doc with a resolved open item, a failed open item and a failed item with no viewer, all carrying sentinels.
    fn doc() -> (Doc, [ItemId; 3]) {
        let ids = [(); 3].map(|()| ItemId::generate());
        let [shown, failed, closed] = ids;
        let content = Content::SeekableVideo(SeekableVideoContent {
            id: ContentId::of_item(&shown),
            item_id: shown,
            display_name: "gate.mp4".into(),
            source: FileVideoSource {
                path: "/home/SECRET-PATH-A/gate.mp4".into(),
            },
        });
        let failure = |folder: &str| {
            let reason = "SECRET-REASON rtsp://root:SECRET-PASSWORD@camera";
            UserError::new(ErrorCode::FileUnreadable, reason)
                .at(HostPath::new(format!("/home/{folder}/lost.mp4")))
        };
        let mut state = State::default();
        state.workspace.name = Some("Review".into());
        state.workspace.items = vec![
            item(shown, "gate.mp4", "SECRET-PATH-A"),
            item(failed, "lost.mp4", "SECRET-PATH-B"),
            item(closed, "closed.mp4", "SECRET-PATH-C"),
        ];
        state.resolutions.insert(
            shown,
            Resolution::Resolved {
                content,
                generation: ContentGen(1),
            },
        );
        state
            .resolutions
            .insert(failed, Resolution::Failed(failure("SECRET-PATH-B")));
        state
            .resolutions
            .insert(closed, Resolution::Failed(failure("SECRET-PATH-C")));
        state.open.extend([shown, failed]);
        (Doc { workspace: state }, ids)
    }

    #[test]
    fn a_watcher_learns_no_path_credential_or_reason_and_sees_only_what_is_open() {
        let (doc, [_, failed, _]) = doc();
        let public = Audience {
            host_details: false,
        };
        let watcher = serde_json::to_string(&project(&doc, public)).unwrap();
        for secret in ["SECRET", "/home", "rtsp", "closed.mp4", "location"] {
            assert!(!watcher.contains(secret), "{secret} reached {watcher}");
        }
        for shared in ["gate.mp4", "Review", "FileUnreadable", &failed.to_string()] {
            assert!(watcher.contains(shared), "{shared} missing from {watcher}");
        }

        let owner = serde_json::to_string(&project(&doc, Audience::HOST)).unwrap();
        for host_only in ["SECRET-REASON", "SECRET-PATH-B", "closed.mp4"] {
            assert!(
                owner.contains(host_only),
                "{host_only} missing from {owner}"
            );
        }
    }
}
