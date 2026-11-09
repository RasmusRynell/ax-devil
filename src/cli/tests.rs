use super::{launch::launch_items, *};
use api_types::handlers::PluginsInfo;
use ax_devil::session::Launch;
use serde_json::Value;
use workspace::intake::{DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults};
use workspace::{Source, WorkspaceItem};
use workspace_files::RecoveryFolder;

pub(super) fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
    args::try_parse_from(
        &Handlers::built_in(),
        std::iter::once("ax-devil").chain(args.iter().copied()),
    )
}

/// The stream settings a `live` command starts from, as the settings give them with the `$AX_DEVIL_*` references
/// kept as written.
pub(super) fn stream_defaults() -> Result<LiveDefaults, String> {
    Ok(LiveDefaults {
        host: "$AX_DEVIL_TARGET_ADDR".into(),
        username: "$AX_DEVIL_TARGET_USER".into(),
        password: workspace::Secret::new("$AX_DEVIL_TARGET_PASS"),
        protocol: workspace::Protocol::Https,
        head: 1,
        rtsp_port: 554,
        rtsp_route: workspace::RtspRoute::Direct,
        resolution: String::new(),
        overlay: LiveMode::DataHub,
        rtsp_handler: "ONVIF_XML".into(),
        mqtt: MqttDefaults {
            handler: "ADF_V1_FRAME".into(),
            host: "$AX_DEVIL_MQTT_BROKER_ADDR".into(),
            user: "$AX_DEVIL_MQTT_BROKER_USER".into(),
            password: workspace::Secret::new("$AX_DEVIL_MQTT_BROKER_PASS"),
            port: 1883,
            source_key: "com.axis.scene.frame.v1#1".into(),
        },
        datahub: DataHubDefaults {
            handler: "ADF_V1_FRAME".into(),
            topic: "com.axis.scene.frame.v1".into(),
            channel: 1,
        },
    })
}

/// The items a command line opens, built by the intake like a dialog's.
pub(super) fn items(args: &[&str]) -> Vec<WorkspaceItem> {
    launch_items(
        parse(args).unwrap().command,
        &Handlers::built_in(),
        stream_defaults,
    )
    .unwrap()
}

#[test]
fn a_plain_launch_opens_nothing_and_keeps_the_global_options() {
    let cli = parse(&["--log-level", "debug", "--config", "x.json"]).unwrap();
    assert!(cli.command.is_none());
    assert!(
        launch_items(cli.command, &Handlers::built_in(), stream_defaults)
            .unwrap()
            .is_empty()
    );
    assert_eq!(cli.global.log_level, logging::Level::Debug);
    assert_eq!(cli.global.config, Some(PathBuf::from("x.json")));
    assert_eq!(parse(&[]).unwrap().global.log_level, logging::Level::Info);

    // A global option applies before or after the command.
    let placed: [&[&str]; 3] = [
        &["clear-cache", "--config", "x.json"],
        &["--config", "x.json", "clear-cache"],
        &["--config", "x.json", "catalog", "list"],
    ];
    for args in placed {
        assert_eq!(
            parse(args).unwrap().global.config,
            Some(PathBuf::from("x.json"))
        );
    }
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
fn a_local_overlay_no_single_handler_reads_fails_with_the_correction() {
    let run = |overlay: &str| {
        launch_items(
            parse(&["local", "--video", "a.mp4", "--overlay", overlay])
                .unwrap()
                .command,
            &Handlers::built_in(),
            stream_defaults,
        )
        .unwrap_err()
    };
    let none = run("data.nothing");
    assert!(
        none.contains("data.nothing") && none.contains("ax-devil list-handlers"),
        "{none}"
    );
    let several = run("data.jsonl");
    assert!(
        several.contains("ADF_V1_FRAME")
            && several.contains("ADF_BETA_FRAME")
            && several.contains("--handler-type"),
        "{several}"
    );
}

#[test]
fn open_refuses_a_recovery_copy_the_window_could_not_take_over() {
    let folder = tempfile::tempdir().unwrap();
    let recovery = RecoveryFolder::new(folder.path().to_path_buf());
    let open = |path: &std::path::Path| {
        launch::launch(
            parse(&["open", path.to_str().unwrap()]).unwrap().command,
            &Handlers::built_in(),
            &recovery,
            &workspace::Environment::new(|_| None),
            stream_defaults,
        )
    };
    let copy = recovery.reserve("Work").unwrap();
    let held = copy.path().to_path_buf();
    assert!(open(&held).err().unwrap().contains("another window"));
    drop(copy);
    assert!(
        open(&held).err().unwrap().contains("Work"),
        "empty file is not a workspace"
    );
    let copy = recovery.claim(&held).unwrap();
    copy.write(&workspace::Workspace::default(), None).unwrap();
    drop(copy);
    assert!(matches!(
        open(&held),
        Ok(Some(Launch::Open {
            workspace: None,
            ..
        }))
    ));
    assert!(open(&folder.path().join("gone.ax-devil.workspace")).is_err());
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
    let missing = launch_items(
        parse(&args(&[])).unwrap().command,
        &Handlers::built_in(),
        stream_defaults,
    );
    assert!(
        missing.unwrap_err().starts_with("width:"),
        "the missing option is named"
    );
}

#[test]
fn a_live_host_reference_that_is_unset_or_rejected_is_refused_without_echoing_its_value() {
    let folder = tempfile::tempdir().unwrap();
    let recovery = RecoveryFolder::new(folder.path().to_path_buf());
    let refusal = |args: &[&str], value: Option<&'static str>| {
        let env = workspace::Environment::new(move |_| value.map(str::to_owned));
        launch::launch(
            parse(args).unwrap().command,
            &Handlers::built_in(),
            &recovery,
            &env,
            stream_defaults,
        )
        .err()
        .expect("the stream is refused")
    };
    let camera = ["live", "--host", "$CAM"];
    let broker = [
        "live",
        "--host",
        "cam.local",
        "--overlay",
        "mqtt",
        "--mqtt-host",
        "$CAM",
    ];
    let rejected = Some("user:SECRETPW@cam.local:554");
    for (args, what, name) in [
        (&camera[..], "No device host", "$CAM"),
        (&broker[..], "No MQTT broker host", "$CAM"),
    ] {
        let unset = refusal(args, None);
        assert!(
            unset.starts_with(&format!("{what}: {name} is empty or unset.")),
            "{unset}"
        );
        let bad = refusal(args, rejected);
        assert!(
            bad.starts_with(&format!(
                "{what}: {name} is set but is not a plain host name."
            )),
            "{bad}"
        );
        assert!(!bad.contains("SECRETPW"), "{bad}");
    }
}

#[test]
fn a_live_stream_refuses_camera_head_zero() {
    let command = parse(&["live", "--camera-head", "0"]).unwrap().command;
    let error = launch_items(command, &Handlers::built_in(), stream_defaults).unwrap_err();
    assert_eq!(error, "head: Camera Head must be a positive integer.");
}

#[test]
fn the_device_api_protocol_is_read_in_any_case() {
    assert!(parse(&["live", "--device-api-protocol", "HTTP"]).is_ok());
    assert!(parse(&["live", "--device-api-protocol", "gopher"]).is_err());
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
    // A decoder with a description shows it after its name; the help of a decoder argument offers the same text.
    let uvg = "UVG-VCM — UVG-VCM v1.0 detection/tracking JSON";
    assert!(listing.contains(uvg), "{listing}");
    let help = parse(&["playlist", "folder_pair", "--help"])
        .err()
        .expect("help ends parsing");
    assert!(help.to_string().contains(uvg), "{help}");
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
