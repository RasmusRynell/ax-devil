//! Plugin metadata accepted by the real ingestion boundary must remain readable in Information.
use crate::common::Backend;
use ax_devil::{
    plugins::Plugins,
    session::project::{audience_of, project},
};
use handler_api::{PlaylistResolver, Settings};
use plugins::LoadedPlugin;
use std::sync::Arc;

#[tokio::test]
async fn all_accepted_metadata_remains_readable() {
    if !crate::plugins::python_available() {
        return;
    }
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/plugins/information/ax-devil-plugin.json");
    let resolver = LoadedPlugin::load(&manifest).unwrap().resolvers.remove(0);
    let handlers =
        handlers::Handlers::built_in().with_resolvers([resolver as Arc<dyn PlaylistResolver>]);
    let item = workspace::intake::playlist("information", Settings::new(), &handlers).unwrap();
    let backend = Backend::start_with(false, Plugins::fixed(handlers), |_| {}).await;
    let added = backend.add_item(item).await;
    let workspace::Content::Playlist(content) = &added.content else {
        panic!("playlist")
    };
    let metadata = &content.playlists[0].metadata;
    let bytes = serde_json::to_vec(metadata).unwrap().len();
    assert!(
        (15_000..=16_384).contains(&bytes),
        "near the per-object ingestion budget: {bytes}"
    );
    assert_eq!(metadata.len(), 33);
    assert!(metadata["description"].as_str().unwrap().len() > 200);
    let path = std::path::Path::new(metadata["path"].as_str().unwrap());
    assert!(
        path.is_absolute(),
        "the plugin emits a native absolute path"
    );
    let filename = path.file_name().unwrap().to_str().unwrap();
    assert!(filename.len() > 60 && !filename.chars().any(char::is_whitespace));
    let second = backend
        .state
        .sessions
        .attach(added.owner.session(), true, Default::default())
        .unwrap();
    let mut view = (*added.owner.state().presenter).clone();
    view.sidebar.open = false;
    second.view(view.clone(), 1).unwrap();
    assert_eq!(
        *added.owner.state().presenter,
        view,
        "both Owners drive the Presenter View"
    );
    let shown = |seat: &sessions::session::Handle<api_types::CursorPoint>| {
        let state = seat.state();
        serde_json::to_value(project(
            &state.doc,
            audience_of(&state, seat.seat(), seat.lease()),
            true,
        ))
        .unwrap()
    };
    let first_doc = shown(&added.owner);
    assert_eq!(
        first_doc,
        shown(&second),
        "each Owner receives the same complete Information"
    );
    let fields: Vec<(String, String)> = serde_json::from_value(
        first_doc["workspace"]["items"][0]["resolution"]["content"]["playlists"][0]["metadata"]
            .clone(),
    )
    .unwrap();
    assert_eq!(fields.len(), metadata.len());
    for (key, value) in metadata {
        assert!(
            fields.contains(&(key.clone(), value.as_str().unwrap().to_owned())),
            "missing or truncated {key}"
        );
    }
}
