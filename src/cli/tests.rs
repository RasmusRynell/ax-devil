use super::*;
use serde_json::json;

fn parse(args: &[&str]) -> Result<Invocation, clap::Error> {
    try_parse_from(
        &Handlers::built_in(),
        std::iter::once("ax-devil").chain(args.iter().copied()),
    )
}

fn playlist(args: &[&str]) -> (String, Settings) {
    let invocation = parse(&[&["playlist"], args].concat()).unwrap();
    let Action::Serve(Opens::Playlist { resolver, settings }) = invocation.action else {
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
    let invocation = parse(&["--log-level", "debug", "--config", "x.json"]).unwrap();
    assert!(matches!(invocation.action, Action::Serve(Opens::Nothing)));
    assert_eq!(invocation.log_level, logging::Level::Debug);
    assert_eq!(invocation.config, Some(PathBuf::from("x.json")));
    assert_eq!(parse(&[]).unwrap().log_level, logging::Level::Info);
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
    let Action::Serve(Opens::Video { video, overlays }) = parse(&[
        "local",
        "--video",
        "a.mp4",
        "--overlay",
        "x.txt",
        "--overlay",
        "y.xml",
        "--handler-type",
        "MOT_FILE",
        "--handler-type",
        "ONVIF_XML",
    ])
    .unwrap()
    .action
    else {
        panic!("local opens a video");
    };
    assert_eq!(video, PathBuf::from("a.mp4"));
    let pairs: Vec<_> = overlays
        .iter()
        .map(|overlay| (overlay.path.to_str().unwrap(), overlay.handler.as_deref()))
        .collect();
    assert_eq!(
        pairs,
        [("x.txt", Some("MOT_FILE")), ("y.xml", Some("ONVIF_XML"))]
    );

    let Action::Serve(Opens::Video { overlays, .. }) =
        parse(&["local", "--video", "a.mp4", "--overlay", "x.txt"])
            .unwrap()
            .action
    else {
        panic!("local opens a video");
    };
    assert_eq!(overlays[0].handler, None);

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
}

#[test]
fn list_handlers_prints_every_decoder_and_resolver_without_opening_anything() {
    let invocation = parse(&["list-handlers"]).unwrap();
    assert!(matches!(invocation.action, Action::ListHandlers));

    let listing = handlers_listing(&Handlers::built_in());
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
    let command = |args: &[&str]| match parse(&[&["catalog"], args].concat()).unwrap().action {
        Action::Catalog(command) => command,
        _ => panic!("a catalog command is a catalog action"),
    };
    assert!(matches!(command(&["list"]), CatalogCommand::List));
    assert!(matches!(
        command(&["check", "night.json"]),
        CatalogCommand::Check(path) if path == std::path::Path::new("night.json")
    ));
    assert!(parse(&["catalog"]).is_err());
    assert!(parse(&["catalog", "check"]).is_err());

    let listing = run_catalog(&CatalogCommand::List);
    assert!(listing.ok);
    assert_eq!(listing.text.lines().count(), 6);
    assert!(listing.text.starts_with("built-in:standard"));

    let folder = tempfile::tempdir().unwrap();
    let good = folder.path().join("good.json");
    std::fs::write(&good, include_str!("../../assets/catalogs/minimal.json")).unwrap();
    let report = run_catalog(&CatalogCommand::Check(good));
    assert!(report.ok);
    assert!(report.text.contains("Minimal"), "{}", report.text);

    let bad = folder.path().join("bad.json");
    std::fs::write(&bad, "{ \"metadata\": 3 }").unwrap();
    let report = run_catalog(&CatalogCommand::Check(bad));
    assert!(!report.ok);
    assert!(report.text.contains("/metadata"), "{}", report.text);
}
