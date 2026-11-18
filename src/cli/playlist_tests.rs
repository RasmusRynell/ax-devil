use super::{
    tests::{items, parse},
    *,
};
use serde_json::{Value, json};
use workspace::Source;

fn playlist(args: &[&str]) -> (String, Settings) {
    let Some(Command::Playlist { resolver, settings }) =
        parse(&[&["playlist"], args].concat()).unwrap().command
    else {
        panic!("a playlist command opens a playlist");
    };
    (resolver, settings)
}

fn absolute(path: &str) -> String {
    std::path::absolute(path)
        .unwrap()
        .to_str()
        .unwrap()
        .to_owned()
}

#[test]
fn mot_arguments_become_settings_with_the_root_absolute_and_all_sequences_when_left_out() {
    let (resolver, settings) = playlist(&["mot_challenge", "data/mot"]);
    assert_eq!(resolver, "mot_challenge");
    assert_eq!(
        settings,
        json!({ "root": absolute("data/mot") })
            .as_object()
            .unwrap()
            .clone()
    );

    let (_, settings) = playlist(&[
        "mot_challenge",
        "/data/mot",
        "--sequences",
        "MOT17-02,MOT17-04",
    ]);
    assert_eq!(
        Value::Object(settings),
        json!({ "root": "/data/mot", "sequences": ["MOT17-02", "MOT17-04"] })
    );
}

#[test]
fn folder_pair_arguments_are_positional_folders_then_a_registered_handler() {
    let (_, settings) = playlist(&["folder_pair", "/v", "/o", "--handler-type", "MOT_FILE"]);
    assert_eq!(
        Value::Object(settings),
        json!({ "videos_dir": "/v", "overlays_dir": "/o", "handler_type": "MOT_FILE" })
    );

    let (_, settings) = playlist(&[
        "folder_pair",
        "/v",
        "/o",
        "--handler-type",
        "MOT_FILE",
        "--option",
        "width=64",
        "--option",
        "height=48",
    ]);
    assert_eq!(settings["options"], json!({ "width": 64, "height": 48 }));
}

#[test]
fn an_installed_resolver_is_a_playlist_subcommand_with_its_settings_as_arguments() {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/plugins/python-runs/ax-devil-plugin.json");
    let plugin = ::plugins::LoadedPlugin::load(&manifest).unwrap();
    let handlers = Handlers::built_in().with_resolvers(
        plugin
            .resolvers
            .iter()
            .map(|resolver| Arc::clone(resolver) as Arc<dyn handler_api::PlaylistResolver>),
    );
    let cli = args::try_parse_from(
        &handlers,
        [
            "ax-devil",
            "playlist",
            "python-runs",
            "/data/runs",
            "--runs",
            "a,b",
        ],
    )
    .unwrap();

    let Some(Command::Playlist { resolver, settings }) = cli.command else {
        panic!("a playlist command opens a playlist");
    };
    assert_eq!(resolver, "python-runs");
    assert_eq!(
        Value::Object(settings),
        json!({ "root": "/data/runs", "runs": ["a", "b"] })
    );

    // A required decoder-options setting needs no flag: a decoder without options has none to give.
    let folder = tempfile::tempdir().unwrap();
    let mut manifest: Value = serde_json::from_slice(&std::fs::read(&manifest).unwrap()).unwrap();
    manifest["handlers"][0]["settings"]
        .as_array_mut()
        .unwrap()
        .extend([
            json!({ "key": "decoder", "label": "Decoder", "kind": "handler", "required": true }),
            json!({ "key": "options", "label": "Options", "kind": "decoder_options",
                "handler_field": "decoder", "required": true }),
        ]);
    let manifest_path = folder.path().join("ax-devil-plugin.json");
    std::fs::write(&manifest_path, manifest.to_string()).unwrap();
    std::fs::write(folder.path().join("runs.py"), "").unwrap();
    let plugin = ::plugins::LoadedPlugin::load(&manifest_path).unwrap();
    let handlers = Handlers::built_in().with_resolvers(
        plugin
            .resolvers
            .iter()
            .map(|resolver| Arc::clone(resolver) as Arc<dyn handler_api::PlaylistResolver>),
    );
    let cli = args::try_parse_from(
        &handlers,
        [
            "ax-devil",
            "playlist",
            "python-runs",
            "/r",
            "--decoder",
            "MOT_FILE",
        ],
    )
    .unwrap();
    let Some(Command::Playlist { settings, .. }) = cli.command else {
        panic!("a playlist command opens a playlist");
    };
    assert_eq!(settings["options"], json!({}));

    // A setting may not repeat an argument every command takes; the plugins crate keeps that list.
    use clap::CommandFactory;
    for arg in Cli::command()
        .get_arguments()
        .filter(|arg| arg.is_global_set())
    {
        let id = arg.get_id().as_str().replace('_', "-");
        let long = arg.get_long().expect("a global option is a flag");
        for name in [id.as_str(), long] {
            assert!(
                ::plugins::RESERVED_SETTING_KEYS.contains(&name),
                "RESERVED_SETTING_KEYS lacks '{name}'"
            );
        }
    }
}

#[test]
fn a_playlist_command_line_becomes_the_item_the_dialog_would_add() {
    let items = items(&["playlist", "mot_challenge", "/data/mot"]);
    let Source::Playlist(recipe) = &items[0].recipe.source else {
        panic!("playlist opens a playlist");
    };
    assert_eq!(recipe.resolver, "mot_challenge");
    assert_eq!(items[0].label, "");
}
