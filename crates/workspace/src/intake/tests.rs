use super::*;
use crate::{Content, ContentId, Environment, VideoRef};
use foundation::{CancelToken, Interrupt, Location};
use handler_api::{Field, FieldKind, Form, Values};
use std::fs;
use std::sync::Arc;

#[test]
fn an_unnamed_video_follows_its_path_when_edited() {
    let mut item = video(Path::new("recordings/gate.mp4"), &[], &Handlers::built_in()).unwrap();
    assert!(item.label.is_empty());
    assert_eq!(item.display_name(), "gate.mp4");
    let Source::Video(recipe) = &item.recipe.source else {
        panic!("a video item has a video recipe");
    };
    assert!(recipe.video.as_path().is_absolute());
    assert!(recipe.video.as_path().ends_with("recordings/gate.mp4"));
    let again = video(Path::new("recordings/gate.mp4"), &[], &Handlers::built_in()).unwrap();
    assert_ne!(item.id, again.id);
    item.recipe = video(Path::new("recordings/yard.mp4"), &[], &Handlers::built_in())
        .unwrap()
        .recipe;
    assert_eq!(item.display_name(), "yard.mp4");
    assert!(item.label.is_empty());
}

#[test]
fn input_that_names_no_file_is_refused_with_the_path_it_was_given() {
    for path in ["", "..", "recordings/.."] {
        let error = video(Path::new(path), &[], &Handlers::built_in()).unwrap_err();
        assert_eq!(error.location, Some(HostPath::new(path).into()), "{path:?}");
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
        .resolve(&handlers, &Environment::of_process(), &CancelToken::new())
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
            item.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
        else {
            panic!("an unreadable path fails to resolve");
        };
        assert_eq!(error.location, Some(HostPath::new(&unreadable).into()));
        assert!(!error.reason.is_empty());
    }
}

/// A decoder that reads `.acme.dat` files and takes a `calib` file option; it never decodes.
struct Acme(handler_api::Spec);

impl Acme {
    fn handlers() -> Handlers {
        let option = Field::new(
            "calib",
            "Calibration",
            FieldKind::File { extensions: vec![] },
        );
        let mot = Handlers::built_in()
            .decoder("MOT_FILE")
            .unwrap()
            .spec()
            .clone();
        let spec = handler_api::Spec {
            id: "ACME".into(),
            extensions: vec![".acme.dat".into()],
            options: Form {
                fields: vec![option],
            },
            ..mot
        };
        Handlers::built_in().with_decoders([Arc::new(Self(spec)) as Arc<dyn handler_api::Decoder>])
    }
}

impl handler_api::Decoder for Acme {
    fn spec(&self) -> &handler_api::Spec {
        &self.0
    }

    fn decode(
        &self,
        _input: &mut dyn handler_api::Input,
        _options: &Values,
        _sink: &mut dyn handler_api::SceneSink,
        _cancel: &CancelToken,
    ) -> Result<handler_api::DecodeSummary, Interrupt<handler_api::DecodeError>> {
        Err(Interrupt::Cancelled)
    }
}

pub(super) fn overlay(path: &Path, handler: Option<&str>) -> NewOverlay {
    NewOverlay {
        path: path.to_owned(),
        handler: handler.map(str::to_owned),
        label: None,
        options: Settings::from_iter([
            ("width".to_owned(), 64.into()),
            ("height".to_owned(), 48.into()),
        ]),
    }
}

fn overlay_handlers(item: &WorkspaceItem) -> Vec<&str> {
    let Source::Video(recipe) = &item.recipe.source else {
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
    assert_eq!(several.location, Some(HostPath::new("boxes.xml").into()));
    for candidate in handlers.decoders_for(Path::new("boxes.xml")) {
        assert!(
            several.reason.contains(&candidate.spec().id),
            "{}",
            several.reason
        );
    }
    assert_eq!(
        add("notes.mp3", None).unwrap_err().location,
        Some(HostPath::new("notes.mp3").into())
    );
    assert_eq!(
        add("gt.txt", Some("NOPE")).unwrap_err().reason,
        "No overlay format \"NOPE\""
    );
}

#[test]
fn an_overlay_keeps_the_options_its_decoder_declares_and_is_refused_for_a_missing_or_unknown_one() {
    let handlers = Handlers::built_in();
    let with = |options: Settings| {
        let overlay = NewOverlay {
            options,
            ..overlay(Path::new("gt.txt"), None)
        };
        video(Path::new("gate.mp4"), &[overlay], &handlers)
    };
    let mut options = overlay(Path::new("gt.txt"), None).options;

    let item = with(options.clone()).unwrap();
    let Source::Video(recipe) = &item.recipe.source else {
        panic!("a video item has a video recipe");
    };
    assert_eq!(recipe.overlays[0].options, options);

    let mut missing = options.clone();
    missing.remove("width");
    assert_eq!(
        with(missing).unwrap_err().location,
        Some(Location::field("width"))
    );
    options.insert("nope".into(), 1.into());
    assert_eq!(
        with(options).unwrap_err().location,
        Some(Location::field("nope"))
    );

    // A file option is stored absolute, and a multi-dot extension finds its decoder.
    let acme = Acme::handlers();
    let calibrated = NewOverlay {
        options: Settings::from_iter([("calib".to_owned(), "calib.json".into())]),
        ..overlay(Path::new("run.ACME.dat"), None)
    };
    let item = video(Path::new("gate.mp4"), &[calibrated], &acme).unwrap();
    let Source::Video(recipe) = &item.recipe.source else {
        panic!("a video item has a video recipe");
    };
    assert_eq!(recipe.overlays[0].handler, "ACME");
    let calib = recipe.overlays[0].options["calib"].as_str().unwrap();
    assert!(
        Path::new(calib).is_absolute() && calib.ends_with("calib.json"),
        "{calib}"
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
        item.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
    };

    let Ok(Content::SeekableVideo(content)) = resolve(&[overlay(&gt, None), overlay(&det, None)])
    else {
        panic!("readable files resolve");
    };
    assert_eq!(content.entry.lane_labels(), ["gt", "det"]);
    assert_eq!(content.entry.overlays[0].handler, "MOT_FILE");

    let Err(Interrupt::Failed(missing)) =
        resolve(&[overlay(&folder.path().join("none.txt"), None)])
    else {
        panic!("a missing overlay fails");
    };
    assert_eq!(
        missing.location,
        Some(HostPath::new(folder.path().join("none.txt")).into())
    );
}

#[test]
fn one_file_may_be_several_lanes_named_by_the_caller_or_numbered_from_the_file_name() {
    let handlers = Handlers::built_in();
    let labelled = |label: &str| NewOverlay {
        label: Some(label.to_owned()),
        ..overlay(Path::new("gt.txt"), None)
    };
    let lanes = |overlays: &[NewOverlay]| {
        video(Path::new("gate.mp4"), overlays, &handlers).map(|item| {
            let Source::Video(recipe) = item.recipe.source else {
                panic!("a video item has a video recipe");
            };
            recipe
                .overlays
                .into_iter()
                .map(|overlay| overlay.label)
                .collect::<Vec<_>>()
        })
    };
    let plain = overlay(Path::new("gt.txt"), None);

    assert_eq!(
        lanes(&[plain.clone(), plain.clone(), plain.clone()]).unwrap(),
        ["gt", "gt (2)", "gt (3)"]
    );
    let named_like_a_number = overlay(Path::new("gt (2).txt"), None);
    assert_eq!(
        lanes(&[
            named_like_a_number.clone(),
            plain.clone(),
            plain.clone(),
            named_like_a_number
        ])
        .unwrap(),
        ["gt (2)", "gt", "gt (3)", "gt (2) (2)"],
        "a generated name never takes one already in use"
    );
    assert_eq!(
        lanes(&[plain.clone(), labelled("gt")]).unwrap(),
        ["gt (2)", "gt"],
        "a name the caller gives a later overlay is kept clear of too"
    );
    assert_eq!(
        lanes(&[plain.clone(), labelled(" Night "), labelled("Day")]).unwrap(),
        ["gt", "Night", "Day"]
    );
}

pub(super) fn mot_folder(sequences: &[&str]) -> tempfile::TempDir {
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

pub(super) fn mot_settings(folder: &Path) -> Settings {
    let mut settings = Settings::new();
    settings.insert("root".into(), folder.display().to_string().into());
    settings
}

/// A resolver with a folder setting that defaults to a relative path.
struct Runs(handler_api::ResolverSpec);

impl handler_api::PlaylistResolver for Runs {
    fn spec(&self) -> &handler_api::ResolverSpec {
        &self.0
    }

    fn resolve(
        &self,
        _settings: &Values,
        _cancel: &CancelToken,
    ) -> Result<handler_api::ResolvedPlaylists, Interrupt<UserError>> {
        Err(Interrupt::Cancelled)
    }
}

#[test]
fn a_playlist_resolves_to_entries_whose_ids_are_the_same_every_time() {
    let folder = mot_folder(&["SEQ-02", "SEQ-01"]);
    let handlers = Handlers::built_in();
    let item = playlist("mot_challenge", mot_settings(folder.path()), &handlers).unwrap();
    assert_eq!(
        (item.label.as_str(), item.display_name().as_str()),
        ("", "mot_challenge"),
        "a default name is derived, not stored"
    );
    let resolve = || {
        let Ok(Content::Playlist(content)) =
            item.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
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
        [
            format!("{}/MOT Challenge/SEQ-01", item.id),
            format!("{}/MOT Challenge/SEQ-02", item.id)
        ]
    );
    let [span] = first.playlists.as_slice() else {
        panic!("one resolver run, one playlist");
    };
    assert_eq!(
        (span.id.to_string(), span.name.as_str(), span.entries),
        (format!("{}/MOT Challenge", item.id), "MOT Challenge", 2)
    );
    assert_eq!(first.entries[0].lane_labels(), ["GT"]);
    assert!(
        Content::Playlist(first)
            .entry(&ContentId::of_entry(&item.id, "MOT Challenge", "SEQ-02"))
            .is_some_and(|entry| entry.label == "SEQ-02")
    );
}

#[test]
fn a_playlist_the_schema_or_resolver_refuses_is_refused_naming_the_setting() {
    let handlers = Handlers::built_in();
    let none = playlist("mot_challenge", Settings::new(), &handlers).unwrap_err();
    assert_eq!(none.location, Some(Location::field("root")));
    let nope = playlist("nope", Settings::new(), &handlers).unwrap_err();
    assert_eq!(nope.code, ErrorCode::UnknownHandler);
    assert_eq!(nope.reason, "Playlist resolver 'nope' is not installed.");
    let settings = Settings::from_iter([
        ("videos_dir".to_owned(), "v".into()),
        ("overlays_dir".to_owned(), "o".into()),
        ("handler_type".to_owned(), "NOPE".into()),
    ]);
    let unknown = playlist("folder_pair", settings.clone(), &handlers).unwrap_err();
    assert_eq!(unknown.code, ErrorCode::UnknownHandler);
    assert_eq!(unknown.location, Some(Location::field("handler_type")));
    let recipe = Recipe::new(Source::Playlist(PlaylistRecipe {
        resolver: "folder_pair".into(),
        settings,
    }));
    let Err(Interrupt::Failed(unknown)) = recipe.resolve(
        &ItemId::generate(),
        "item",
        &handlers,
        &Environment::of_process(),
        &CancelToken::new(),
    ) else {
        panic!("an unknown overlay format fails to resolve");
    };
    assert_eq!(unknown.code, ErrorCode::UnknownHandler);

    let gone = playlist(
        "mot_challenge",
        mot_settings(Path::new("no/such/folder")),
        &handlers,
    )
    .unwrap();
    assert_eq!(gone.label, "");
    let gone = with_label(gone, Some(" Mine "), &Environment::literal());
    assert_eq!(gone.label, " Mine ");
    assert_eq!(
        with_label(gone.clone(), Some("  "), &Environment::literal()).label,
        "  "
    );
    for default in ["", "mot_challenge"] {
        assert_eq!(
            with_label(gone.clone(), Some(default), &Environment::literal()).label,
            "",
            "{default:?} clears the label"
        );
    }
    let Source::Playlist(recipe) = &gone.recipe.source else {
        panic!("a playlist item has a playlist recipe");
    };
    assert!(Path::new(recipe.settings["root"].as_str().unwrap()).is_absolute());
    let Err(Interrupt::Failed(error)) =
        gone.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
    else {
        panic!("a missing folder fails to resolve");
    };
    assert!(
        matches!(&error.location, Some(Location::Host(path)) if path.as_path().ends_with("no/such/folder")),
        "{error:?}"
    );
    assert!(
        error
            .reason
            .starts_with("Playlist resolver 'mot_challenge' failed: "),
        "{error:?}"
    );

    // A folder left at its relative default is stored as that folder in the working folder, and a key the schema
    // does not declare is stored as given.
    let runs = handler_api::ResolverSpec {
        id: "runs".into(),
        plugin: "runs".into(),
        name: "Runs".into(),
        description: String::new(),
        form: Form {
            fields: vec![Field {
                default: Some("runs".into()),
                ..Field::new("dir", "Folder", FieldKind::Folder)
            }],
        },
    };
    let handlers =
        handlers.with_resolvers([Arc::new(Runs(runs)) as Arc<dyn handler_api::PlaylistResolver>]);
    let item = playlist(
        "runs",
        Settings::from_iter([("note".to_owned(), "kept".into())]),
        &handlers,
    )
    .unwrap();
    let Source::Playlist(recipe) = &item.recipe.source else {
        panic!("a playlist item has a playlist recipe");
    };
    assert_eq!(
        Path::new(recipe.settings["dir"].as_str().unwrap()),
        std::env::current_dir().unwrap().join("runs")
    );
    assert_eq!(recipe.settings["note"], "kept");
}

#[test]
fn a_folder_pair_gives_every_overlay_the_options_of_its_decoder_and_is_refused_without_a_required_one()
 {
    let dir = tempfile::tempdir().unwrap();
    for file in ["v/clip.mp4", "o/clip.txt"] {
        fs::create_dir_all(dir.path().join(file).parent().unwrap()).unwrap();
        fs::write(dir.path().join(file), "data").unwrap();
    }
    let handlers = Handlers::built_in();
    let settings = |options: Option<Settings>| {
        let mut settings = Settings::from_iter([
            (
                "videos_dir".to_owned(),
                dir.path().join("v").to_str().unwrap().into(),
            ),
            (
                "overlays_dir".to_owned(),
                dir.path().join("o").to_str().unwrap().into(),
            ),
            ("handler_type".to_owned(), "MOT_FILE".into()),
        ]);
        settings.extend(options.map(|options| ("options".to_owned(), options.into())));
        settings
    };

    let error = playlist("folder_pair", settings(None), &handlers).unwrap_err();
    assert_eq!(error.location, Some(Location::field("options")));
    assert!(
        error.reason.contains("Image width") && error.reason.contains("Required"),
        "{error}"
    );

    let options = Settings::from_iter([
        ("width".to_owned(), 64.into()),
        ("height".to_owned(), 48.into()),
    ]);
    let item = playlist("folder_pair", settings(Some(options.clone())), &handlers).unwrap();
    let Ok(Content::Playlist(content)) =
        item.resolve(&handlers, &Environment::of_process(), &CancelToken::new())
    else {
        panic!("a folder pair with the options resolves");
    };
    assert_eq!(content.entries[0].overlays[0].options, options);
}
