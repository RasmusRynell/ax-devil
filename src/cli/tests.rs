use super::*;
use serde_json::{Value, json};
use workspace::Recipe;

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
        "--overlay",
        &onvif,
        "--handler-type",
        "MOT_FILE",
        "--handler-type",
        "ONVIF_XML",
    ])[0];
    let Recipe::Video(recipe) = &item.recipe else {
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

    let Recipe::Video(recipe) =
        &items(&["local", "--video", &video, "--overlay", &tracks])[0].recipe
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
fn a_playlist_command_line_becomes_the_item_the_dialog_would_add() {
    let items = items(&["playlist", "mot_challenge", "/data/mot"]);
    let Recipe::Playlist(recipe) = &items[0].recipe else {
        panic!("playlist opens a playlist");
    };
    assert_eq!(recipe.resolver, "mot_challenge");
    assert_eq!(items[0].label, "MOT Challenge");
}

#[test]
fn list_handlers_prints_every_decoder_and_resolver_without_opening_anything() {
    assert!(matches!(
        parse(&["list-handlers"]).unwrap().command,
        Some(Command::ListHandlers)
    ));

    let listing = args::handlers_listing(&Handlers::built_in());
    for expected in ["MOT_FILE", "mot_challenge", "folder_pair", "MOT Challenge"] {
        assert!(listing.contains(expected), "{expected} in\n{listing}");
    }
    let line = listing
        .lines()
        .find(|line| line.contains("MOT_FILE"))
        .unwrap();
    assert!(line.starts_with("  MOT_FILE"), "{line}");
}

#[test]
fn catalog_commands_list_the_built_ins_and_check_a_file_with_its_problems_located() {
    let command = |args: &[&str]| match parse(&[&["catalog"], args].concat()).unwrap().command {
        Some(Command::Catalog(command)) => command,
        _ => panic!("a catalog command is a catalog command"),
    };
    assert!(matches!(command(&["list"]), CatalogCommand::List));
    assert!(matches!(
        command(&["check", "night.json"]),
        CatalogCommand::Check { file } if file == std::path::Path::new("night.json")
    ));
    assert!(parse(&["catalog"]).is_err());
    assert!(parse(&["catalog", "check"]).is_err());

    let catalogs = tempfile::tempdir().unwrap();
    std::fs::write(
        catalogs.path().join("night.json"),
        r#"{"name":"Night","description":"Dark"}"#,
    )
    .unwrap();
    let folder = || Ok(catalogs.path().to_path_buf());
    let listing = catalog::run(&CatalogCommand::List, folder());
    assert!(listing.ok);
    assert_eq!(listing.text.lines().count(), 7);
    assert!(listing.text.starts_with("built-in:standard"));
    assert!(
        listing.text.ends_with("user:night.json    Night: Dark\n"),
        "{}",
        listing.text
    );
    let unknown = catalog::run(&CatalogCommand::List, Err("no home".into()));
    assert!(!unknown.ok);
    assert!(unknown.text.contains("no home"));
    // A user catalog is checked by its id, in the folder, without the settings.
    let checked = |file: &str, folder| catalog::checked_file(std::path::Path::new(file), folder);
    assert_eq!(
        checked("user:night.json", folder()),
        Ok(catalogs.path().join("night.json"))
    );
    assert!(checked("user:night.json", Err("no home".into())).is_err());
    assert_eq!(
        checked("night.json", Err("no home".into())),
        Ok("night.json".into())
    );
    assert_eq!(
        checked("user:../night.json", folder()),
        Ok("user:../night.json".into())
    );

    // The checker is the frontend's compiler, run in Electron-as-Node from the checkout's desktop folder.
    let desktop = std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/desktop"));
    let folder = tempfile::tempdir().unwrap();
    let good = folder.path().join("good.json");
    std::fs::write(&good, include_str!("../../assets/catalogs/minimal.v4.json")).unwrap();
    let report = catalog::check_in(desktop, &good).unwrap();
    assert!(report.ok, "{}", report.text);
    assert!(report.text.contains("Minimal"), "{}", report.text);

    let bad = folder.path().join("bad.json");
    std::fs::write(
        &bad,
        include_str!("../../assets/catalogs/minimal.v4.json")
            .replace("\"stroke\": \"color\"", "\"stroke\": \"colr\""),
    )
    .unwrap();
    let report = catalog::check_in(desktop, &bad).unwrap();
    assert!(!report.ok);
    assert!(
        report
            .text
            .contains("/layers/0/marks/0/stroke: unknown name 'colr'"),
        "{}",
        report.text
    );
    assert!(
        report.text.contains("  17:"),
        "line and column: {}",
        report.text
    );

    let report = catalog::check_in(desktop, &folder.path().join("missing.json")).unwrap();
    assert!(!report.ok);
    assert!(report.text.contains("missing.json"), "{}", report.text);

    let error = catalog::check_in(folder.path(), &good).unwrap_err();
    assert!(
        error.contains("Cannot find the desktop shell's Electron"),
        "{error}"
    );
}
