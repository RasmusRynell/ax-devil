use super::{launch::launch_items, *};
use api_types::handlers::PluginsInfo;
use serde_json::{Value, json};
use workspace::{Source, WorkspaceItem};

fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
    args::try_parse_from(
        &Handlers::built_in(),
        std::iter::once("ax-devil").chain(args.iter().copied()),
    )
}

/// The items a command line opens, built by the intake like a dialog's.
fn items(args: &[&str]) -> Vec<WorkspaceItem> {
    launch_items(parse(args).unwrap().command, &Handlers::built_in()).unwrap()
}

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
fn a_plain_launch_opens_nothing_and_keeps_the_global_options() {
    let cli = parse(&["--log-level", "debug", "--config", "x.json"]).unwrap();
    assert!(cli.command.is_none());
    assert!(
        launch_items(cli.command, &Handlers::built_in())
            .unwrap()
            .is_empty()
    );
    assert_eq!(cli.global.log_level, logging::Level::Debug);
    assert_eq!(cli.global.config, Some(PathBuf::from("x.json")));
    assert_eq!(parse(&[]).unwrap().global.log_level, logging::Level::Info);
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
fn missing_or_unknown_arguments_are_usage_errors() {
    let refused: [&[&str]; 7] = [
        &["playlist"],
        &["playlist", "nope"],
        &["playlist", "mot_challenge"],
        &["playlist", "mot_challenge", ""],
        &["playlist", "folder_pair", "/v", "/o"],
        &[
            "playlist",
            "folder_pair",
            "/v",
            "/o",
            "--handler-type",
            "NOPE",
        ],
        &["playlist", "mot_challenge", "/d", "--unknown"],
    ];
    for args in refused {
        assert!(parse(args).is_err(), "{args:?}");
    }
}

#[test]
fn local_pairs_overlays_with_handlers_in_order_or_leaves_them_all_to_the_extension() {
    let folder = tempfile::tempdir().unwrap();
    let path = |name: &str| folder.path().join(name).to_str().unwrap().to_owned();
    let [video, tracks, onvif] = ["a.mp4", "x.txt", "y.xml"].map(path);
    let item = &items(&[
        "local",
        "--video",
        &video,
        "--overlay",
        &tracks,
        "--overlay-option",
        "width=64",
        "--overlay-option",
        "height=48",
        "--overlay",
        &onvif,
        "--handler-type",
        "MOT_FILE",
        "--handler-type",
        "ONVIF_XML",
    ])[0];
    let Source::Video(recipe) = &item.recipe.source else {
        panic!("local opens a video");
    };
    assert_eq!(item.label, "a.mp4");
    let pairs: Vec<_> = recipe
        .overlays
        .iter()
        .map(|overlay| {
            (
                overlay.path.as_path().to_str().unwrap(),
                overlay.handler.as_str(),
            )
        })
        .collect();
    assert_eq!(
        pairs,
        [(tracks.as_str(), "MOT_FILE"), (onvif.as_str(), "ONVIF_XML")]
    );

    let Source::Video(recipe) = &items(&[
        "local",
        "--video",
        &video,
        "--overlay",
        &tracks,
        "--overlay-option",
        "width=64",
        "--overlay-option",
        "height=48",
    ])[0]
        .recipe
        .source
    else {
        panic!("local opens a video");
    };
    assert_eq!(
        recipe.overlays[0].handler, "MOT_FILE",
        "picked by extension"
    );

    let uneven = [
        "local",
        "--video",
        "a",
        "--overlay",
        "x",
        "--overlay",
        "y",
        "--handler-type",
        "MOT_FILE",
    ];
    assert!(parse(&uneven).is_err(), "some but not all handlers named");
    assert!(
        parse(&["local", "--overlay", "x"]).is_err(),
        "--video is required"
    );
    assert!(
        parse(&[
            "local",
            "--video",
            "a",
            "--overlay",
            "x",
            "--handler-type",
            "NOPE"
        ])
        .is_err(),
        "the handler must exist"
    );
}

#[test]
fn an_overlay_option_belongs_to_the_nearest_preceding_overlay_and_is_typed_by_its_decoder() {
    let args = |first: &[&'static str]| {
        let head = ["local", "--video", "a.mp4", "--overlay", "x.txt"];
        let tail = [
            "--overlay",
            "y.txt",
            "--overlay-option",
            "width=32",
            "--overlay-option",
            "height=24",
        ];
        [&head[..], first, &tail[..]].concat()
    };
    let both = args(&[
        "--overlay-option",
        "width=64",
        "--overlay-option",
        "height=48",
    ]);
    let [item] = &mut items(&both)[..] else {
        panic!("local opens one video");
    };
    let Source::Video(recipe) = &item.recipe.source else {
        panic!("local opens a video");
    };
    assert_eq!(
        recipe.overlays[0].options["width"], 64,
        "a number, as the decoder declares"
    );
    assert_eq!(recipe.overlays[0].options["height"], 48);
    assert_eq!(recipe.overlays[1].options["width"], 32);

    let early = [
        "local",
        "--overlay-option",
        "width=1",
        "--video",
        "a.mp4",
        "--overlay",
        "x.txt",
    ];
    assert!(parse(&early).is_err(), "no overlay precedes it");
    let missing = launch_items(parse(&args(&[])).unwrap().command, &Handlers::built_in());
    assert!(
        missing.unwrap_err().starts_with("width:"),
        "the missing option is named"
    );
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

#[test]
fn list_handlers_prints_every_decoder_and_resolver_without_opening_anything() {
    assert!(matches!(
        parse(&["list-handlers"]).unwrap().command,
        Some(Command::ListHandlers)
    ));

    let listing = args::handlers_listing(&Handlers::built_in(), &PluginsInfo::default());
    for expected in ["MOT_FILE", "mot_challenge", "folder_pair", "MOT Challenge"] {
        assert!(listing.contains(expected), "{expected} in\n{listing}");
    }
    let line = listing
        .lines()
        .find(|line| line.contains("MOT_FILE"))
        .unwrap();
    assert!(line.starts_with("  MOT_FILE"), "{line}");
}

/// The catalog commands on a fresh settings file and catalog folder in `dir`, as a new launch reads them.
fn catalog(dir: &std::path::Path, args: &[&str]) -> catalog::Report {
    let Some(Command::Catalog(command)) = parse(&[&["catalog"], args].concat()).unwrap().command
    else {
        panic!("a catalog command is a catalog command");
    };
    let home = catalog::Home {
        folder: dir.join("catalogs"),
        settings: ::settings::Settings::load(&dir.join("settings.json")).unwrap(),
    };
    catalog::run(&command, Ok(home))
}

fn default_catalog(dir: &std::path::Path) -> Value {
    ::settings::Settings::load(&dir.join("settings.json"))
        .unwrap()
        .state()
        .resolved("overlays.default_catalog")
        .unwrap()
}

#[test]
fn catalog_new_copies_without_overwriting_and_leaves_the_default_alone() {
    assert!(parse(&["catalog"]).is_err());
    assert!(parse(&["catalog", "render", "night.json"]).is_err());
    let dir = tempfile::tempdir().unwrap();
    let file = |name: &str| dir.path().join("catalogs").join(name);

    let first = catalog(dir.path(), &["new", "Night Shift"]);
    assert_eq!(
        first.text,
        format!("{}\n", file("night-shift.json").display())
    );
    let second = catalog(
        dir.path(),
        &["new", "Night Shift", "--from", "built-in:tracking"],
    );
    assert_eq!(
        second.text,
        format!("{}\n", file("night-shift-2.json").display())
    );
    assert!(first.ok && second.ok && file("night-shift.json").is_file());
    assert_eq!(default_catalog(dir.path()), "");

    let listing = catalog(dir.path(), &["list"]).text;
    let lines: Vec<&str> = listing.lines().collect();
    assert!(lines[0].starts_with("* Standard") && lines[0].ends_with("(built-in, read-only)"));
    for (line, file) in lines[6..]
        .iter()
        .zip(["night-shift-2.json", "night-shift.json"])
    {
        assert!(
            line.contains("Night Shift") && line.ends_with(file),
            "{listing}"
        );
    }
    assert_eq!(lines.len(), 8);
    assert!(!catalog(dir.path(), &["new", "Blank", "--from", "built-in:nope"]).ok);
}

#[test]
fn catalog_use_stores_one_setting_and_standard_clears_it() {
    let dir = tempfile::tempdir().unwrap();
    let personal = || {
        let state = ::settings::Settings::load(&dir.path().join("settings.json"))
            .unwrap()
            .state();
        state.values(&[::settings::Scope::Personal])
    };
    let before = personal();

    let used = catalog(dir.path(), &["use", "built-in:tracking"]);
    assert!(used.ok, "{}", used.text);
    assert_eq!(used.text, "Default catalog: built-in:tracking\n");
    assert_eq!(default_catalog(dir.path()), "built-in:tracking");
    assert!(catalog(dir.path(), &["list"]).text.contains("* Tracking"));
    let mut changed = personal();
    changed.insert("overlays.default_catalog".into(), "".into());
    assert_eq!(changed, before, "no other setting changed");

    assert!(catalog(dir.path(), &["use", "built-in:standard"]).ok);
    assert_eq!(default_catalog(dir.path()), "");
}

#[test]
fn catalog_use_refuses_a_file_outside_the_catalog_folder() {
    let dir = tempfile::tempdir().unwrap();
    let outside = dir.path().join("outside.json");
    std::fs::write(&outside, r#"{"name":"Outside","description":"Elsewhere"}"#).unwrap();
    let refused = catalog(dir.path(), &["use", outside.to_str().unwrap()]);
    assert!(!refused.ok);
    assert!(refused.text.contains("catalog folder"), "{}", refused.text);
    assert_eq!(default_catalog(dir.path()), "");
    assert!(!dir.path().join("catalogs").exists());
}

#[cfg(unix)]
#[test]
fn catalog_use_refuses_a_linked_catalog_the_app_would_not_serve() {
    let dir = tempfile::tempdir().unwrap();
    let folder = dir.path().join("catalogs");
    std::fs::create_dir(&folder).unwrap();
    let target = dir.path().join("elsewhere.json");
    std::fs::write(&target, r#"{"name":"Linked","description":"A link"}"#).unwrap();
    std::os::unix::fs::symlink(&target, folder.join("linked.json")).unwrap();
    let before = default_catalog(dir.path());

    let refused = catalog(dir.path(), &["use", "user:linked.json"]);
    assert!(!refused.ok, "{}", refused.text);
    assert!(refused.text.contains("linked.json"), "{}", refused.text);
    assert_eq!(default_catalog(dir.path()), before);
}

#[tokio::test]
async fn plugins_installed_updated_and_removed_through_the_registry_are_listed_and_usable_by_the_next_command()
 {
    let folder = tempfile::tempdir().unwrap();
    let registry = Ok(folder.path().join("data/plugins.json"));
    let fixture =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/plugins/shell-times");
    let broken = folder.path().join("broken");
    std::fs::create_dir(&broken).unwrap();
    std::fs::write(
        broken.join("ax-devil-plugin.json"),
        r#"{"manifest":2,"protocol":3}"#,
    )
    .unwrap();
    // Each command starts as the app does: the registry is read before the command line.
    let plugins = |args: &[&str]| {
        let command = match parse(&[&["plugins"], args].concat()).unwrap().command {
            Some(Command::Plugins(command)) => command,
            _ => panic!("a plugins command is a plugins command"),
        };
        plugins::run(
            command,
            Plugins::new(registry.clone(), Handlers::built_in()),
        )
    };
    let fixture = fixture.to_str().unwrap();

    let installed = plugins(&["install", fixture]).await;
    assert!(
        installed.ok && installed.text.contains("Installed shell-times 1.0.0"),
        "{}",
        installed.text
    );
    let again = plugins(&["install", fixture]).await;
    assert!(
        !again.ok && again.text.contains("already taken"),
        "{}",
        again.text
    );
    let refused = plugins(&["install", broken.to_str().unwrap()]).await;
    assert!(!refused.ok, "{}", refused.text);

    let listed = plugins(&["list"]).await;
    assert!(
        listed.ok && listed.text.starts_with("shell-times  1.0.0  .times.txt"),
        "{}",
        listed.text
    );
    let json: Value = serde_json::from_str(&plugins(&["list", "--json"]).await.text).unwrap();
    assert_eq!(
        json["plugins"].as_array().unwrap().last().unwrap()["status"]["state"],
        "loaded"
    );
    let handlers = Plugins::new(registry.clone(), Handlers::built_in()).handlers();
    assert!(
        args::try_parse_from(
            &handlers,
            [
                "ax-devil",
                "local",
                "--video",
                "v.mp4",
                "--overlay",
                "a.txt",
                "--handler-type",
                "shell-times"
            ]
        )
        .is_ok()
    );

    // Another folder with the same plugin replaces it in place; one that is not valid changes nothing.
    let moved = folder.path().join("moved");
    std::fs::create_dir(&moved).unwrap();
    for file in ["ax-devil-plugin.json", "convert.sh"] {
        std::fs::copy(std::path::Path::new(fixture).join(file), moved.join(file)).unwrap();
    }
    let update =
        |path: &std::path::Path| plugins(&["update", "shell-times", path.to_str().unwrap()]);
    assert!(update(&moved).await.ok);
    assert!(!update(&broken).await.ok);
    let listed = plugins(&["list"]).await;
    assert!(listed.text.contains("moved"), "{}", listed.text);

    assert!(plugins(&["remove", "shell-times"]).await.ok);
    assert!(!plugins(&["remove", "shell-times"]).await.ok);
    assert!(plugins(&["list"]).await.text.contains("No plugins"));
}
