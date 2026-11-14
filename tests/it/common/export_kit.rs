//! What the export tests share beyond the backend and viewer: serving lanes, reading the export's notices, and
//! whether the real Electron render page can run here.

use super::{Added, Backend, Event, Handlers, Plugins, Viewer};
use api_types::playback::{
    CatalogRefWire, ClientMessage, ExportFormatWire, ExportLaneWire, ExportStateWire,
    FilterOptionInfo, LanePolicyWire, LaneStatusWire, ServerMessage, StickyWire,
};
use ax_devil::exports::ExportService;
use catalogs::Library;
use export::LaneStyle;
use foundation::{CancelToken, VideoFrame};
use handler_api::Settings;
use media::{Decoder, FrameIndex, VideoInput};
use scene::MatchBasis;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};
use workspace::intake::NewOverlay;

/// The text messages of the viewer until `pick` takes one, leaving out the frames and data between.
pub async fn until<T>(viewer: &mut Viewer, mut pick: impl FnMut(ServerMessage) -> Option<T>) -> T {
    loop {
        if let Event::Message(message) = viewer.next().await
            && let Some(found) = pick(message)
        {
            return found;
        }
    }
}

/// What the backend served a lane: its catalog's revision, its data revision and its class filters.
pub struct Served {
    pub catalog: u32,
    pub data: u32,
    pub filter: Vec<FilterOptionInfo>,
}

/// Subscribes each lane (matched by time, policy revision 1, keeping the last sample on screen when `sticky`) and
/// asks for the catalog it is paired with; returns once every lane and catalog is served, in the order of `lanes`.
pub async fn serve_lanes(viewer: &mut Viewer, sticky: bool, lanes: &[(u16, &str)]) -> Vec<Served> {
    for (lane, catalog) in lanes {
        viewer
            .send(&ClientMessage::LanePolicy {
                lane: *lane,
                revision: 1,
                policy: LanePolicyWire {
                    basis: Some(MatchBasis::Time),
                    tolerance_ms: None,
                    sticky: StickyWire {
                        enabled: sticky,
                        timeout_ms: None,
                    },
                },
                subscribed: true,
            })
            .await;
        viewer
            .send(&ClientMessage::Catalog {
                id: (*catalog).into(),
            })
            .await;
    }
    let mut data = vec![None; lanes.len()];
    let mut catalogs = std::collections::BTreeMap::new();
    let served = |data: &[Option<(u32, Vec<FilterOptionInfo>)>],
                  catalogs: &std::collections::BTreeMap<String, u32>| {
        data.iter().all(Option::is_some) && lanes.iter().all(|(_, id)| catalogs.contains_key(*id))
    };
    while !served(&data, &catalogs) {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                lane,
                status:
                    LaneStatusWire::Ready {
                        data_revision,
                        filter,
                        ..
                    },
            }) => {
                if let Some(at) = lanes.iter().position(|(wanted, _)| *wanted == lane) {
                    data[at] = Some((data_revision, filter));
                }
            }
            Event::Message(ServerMessage::Catalog { id, revision, .. }) => {
                catalogs.insert(id, revision);
            }
            _ => {}
        }
    }
    lanes
        .iter()
        .zip(data)
        .map(|((_, id), data)| {
            let (data, filter) = data.unwrap();
            Served {
                catalog: catalogs[*id],
                data,
                filter,
            }
        })
        .collect()
}

/// One owner's viewer of a video whose lanes read `overlays` (a file name, which picks the decoder, and its content,
/// per lane) and draw with the catalog (its JSON) of the same position in `catalogs`; the lanes as an export request names them.
pub struct Scene {
    // Serves the viewer until the scene ends.
    _backend: Backend,
    viewer: Viewer,
    lanes: Vec<ExportLaneWire>,
    out: tempfile::TempDir,
    _catalogs: tempfile::TempDir,
}

pub async fn scene(
    video: &str,
    (width, height): (u32, u32),
    overlays: &[(&str, &str)],
    catalogs: &[String],
    sticky: bool,
) -> Scene {
    let folder = tempfile::tempdir().unwrap();
    for (n, catalog) in catalogs.iter().enumerate() {
        fs::write(folder.path().join(format!("c{n}.json")), catalog).unwrap();
    }
    let backend =
        Backend::start_with_catalogs(Library::with_folder(folder.path().to_path_buf())).await;
    let files: Vec<_> = overlays
        .iter()
        .map(|(name, content)| {
            let path = backend.folder.path().join(name);
            fs::write(&path, content).unwrap();
            // Several handlers read .xml; the others are named by their extension. MOT boxes are measured in the
            // picture's size.
            let options = if name.ends_with(".txt") {
                super::mot_options((width, height))
            } else {
                Default::default()
            };
            (path, name.ends_with(".xml").then_some("ONVIF_XML"), options)
        })
        .collect();
    let added = backend
        .add_with_handlers(&super::fixture(video), &files)
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;
    let ids: Vec<_> = (0..overlays.len())
        .map(|n| format!("user:c{n}.json"))
        .collect();
    let wanted: Vec<_> = ids
        .iter()
        .enumerate()
        .map(|(n, id)| (n as u16, id.as_str()))
        .collect();
    let lanes = serve_lanes(&mut viewer, sticky, &wanted)
        .await
        .into_iter()
        .enumerate()
        .map(|(n, served)| ExportLaneWire {
            lane: n as u16,
            catalog: CatalogRefWire {
                id: ids[n].clone(),
                revision: served.catalog,
            },
            style: LaneStyle {
                label: format!("Lane {n}"),
                // As the viewer's menu has them: the file's filters that start on.
                enabled_classes: (0..served.filter.len() as u32)
                    .filter(|at| served.filter[*at as usize].default_enabled)
                    .collect(),
                class_options: served.filter.len() as u16,
                hidden: 0,
                theme: None,
                retained_opacity: 0.5,
                filter: None,
            },
            policy_revision: 1,
            data_revision: Some(served.data),
        })
        .collect();
    Scene {
        _backend: backend,
        viewer,
        lanes,
        out: tempfile::tempdir().unwrap(),
        _catalogs: folder,
    }
}

impl Scene {
    /// Exports frames `first` to `last` as `format` to `name` in the output folder and returns the path once the
    /// export is done.
    pub async fn export(
        &mut self,
        first: u64,
        last: u64,
        format: ExportFormatWire,
        name: &str,
    ) -> std::path::PathBuf {
        let path = self.out.path().join(name);
        self.viewer
            .send(&ClientMessage::Export {
                job: 1,
                lanes: self.lanes.clone(),
                first,
                last,
                format,
                path: path.display().to_string(),
            })
            .await;
        let (_, ended) = finished(&mut self.viewer, 1).await;
        assert_eq!(ended, ExportStateWire::Done);
        path
    }
}

/// The frames of a video file, decoded, and its index.
pub fn decoded(input: &VideoInput) -> (Arc<FrameIndex>, Vec<VideoFrame>) {
    let index =
        Arc::new(FrameIndex::load_or_build(input, None, &CancelToken::new(), &mut |_| {}).unwrap());
    let mut decoder = Decoder::open(input, index.clone(), 1, CancelToken::new()).unwrap();
    let mut frames = Vec::new();
    while let Some(frame) = decoder.next_frame().unwrap() {
        frames.push(frame);
    }
    (index, frames)
}

/// The Y, Cb and Cr samples of pixel (x, y) of a 4:2:0 frame.
pub fn yuv_at(frame: &VideoFrame, x: usize, y: usize) -> [i32; 3] {
    let planes = frame.layout().planes;
    let at = |plane: usize, x: usize, y: usize| {
        i32::from(frame.plane(plane)[y * planes[plane].stride as usize + x])
    };
    [at(0, x, y), at(1, x / 2, y / 2), at(2, x / 2, y / 2)]
}

/// Asserts the sample triple is within `by` of `want` in every component.
pub fn assert_near(got: [i32; 3], want: [i32; 3], by: i32, what: &str) {
    assert!(
        got.iter()
            .zip(want)
            .all(|(got, want)| (got - want).abs() <= by),
        "{what}: {got:?} is not within {by} of {want:?}"
    );
}

/// The state the next `Export` notice of `job` reports, other jobs left out.
pub async fn state_of(viewer: &mut Viewer, job: u32) -> ExportStateWire {
    until(viewer, |message| match message {
        ServerMessage::Export { job: at, state } if at == job => Some(state),
        _ => None,
    })
    .await
}

/// Reads the job's notices until it ends: the progress seen and the last state.
pub async fn finished(viewer: &mut Viewer, job: u32) -> (Vec<u64>, ExportStateWire) {
    let mut progress = Vec::new();
    loop {
        match state_of(viewer, job).await {
            ExportStateWire::Running { done, .. } => progress.push(done),
            ended => return (progress, ended),
        }
    }
}

/// The names of the files in `folder`, sorted.
pub fn files(folder: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(folder)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// Whether this machine can run the real Electron render page and catalog checker, which it also prepares for: the backend and the
/// commands it starts find the checkout's desktop folder (a test binary is not under `target/<profile>/ax-devil`)
/// and the page renders on SwiftShader, so its pixels do not depend on the machine's graphics. When it cannot, says
/// why on stderr and returns false, so the test returns early; with `AX_DEVIL_REQUIRE_ELECTRON` set (the Make
/// targets set it) that is a failure instead.
#[allow(clippy::print_stderr)] // The skip notice: a test cannot be skipped any other way.
pub fn can_export() -> bool {
    static READY: std::sync::OnceLock<Result<(), String>> = std::sync::OnceLock::new();
    let desktop = Path::new(env!("CARGO_MANIFEST_DIR")).join("desktop");
    let ready = READY.get_or_init(|| {
        ax_devil::desktop::electron(&desktop)
            .map_err(|path| ax_devil::desktop::missing_electron(&path))?;
        ax_devil::desktop::render_page(&desktop)?;
        ax_devil::desktop::catalog_checker(&desktop)?;
        // SAFETY: this runs once, before any test of the binary starts a backend or a page; the other tests'
        // threads are blocked on this lock meanwhile, and nothing else reads the environment.
        unsafe {
            std::env::set_var("AX_DEVIL_DESKTOP", &desktop);
            std::env::set_var("AX_DEVIL_RENDER_GRAPHICS", "software");
        }
        Ok(())
    });
    match ready {
        Ok(()) => true,
        Err(_) if std::env::var_os("AX_DEVIL_REQUIRE_ELECTRON").is_some() => {
            panic!("AX_DEVIL_REQUIRE_ELECTRON is set, yet {ready:?}")
        }
        Err(reason) => {
            eprintln!("SKIPPED: cannot export on this machine: {reason}");
            false
        }
    }
}

/// The size and the RGBA rows, tightly packed, of the PNG at `path`.
pub fn read_png(path: &Path) -> (u32, u32, Vec<u8>) {
    // The image sequence reader wants a numbered name; read a copy so the folder of `path` is left alone.
    let folder = tempfile::tempdir().unwrap();
    std::fs::copy(path, folder.path().join("0.png")).unwrap();
    let input = media::VideoInput::ImageSequence(media::ImageSequenceInput {
        pattern: folder.path().join("%d.png"),
        start_number: 0,
        frame_rate: foundation::FrameRate::parse("1").unwrap(),
        frame_count: 1,
    });
    let index = media::FrameIndex::load_or_build(
        &input,
        None,
        &foundation::CancelToken::new(),
        &mut |_| {},
    )
    .unwrap();
    let (width, height) = (index.info().width, index.info().height);
    let mut decoder =
        media::Decoder::open(&input, Arc::new(index), 1, foundation::CancelToken::new()).unwrap();
    let frame = decoder.next_frame().unwrap().unwrap();
    (width, height, frame.plane(0).to_vec())
}

impl Backend {
    /// Like [`Backend::add_with_overlays`], with the handler and options each overlay names, for files whose
    /// extension several handlers read.
    pub async fn add_with_handlers(
        &self,
        path: &Path,
        overlays: &[(PathBuf, Option<&str>, Settings)],
    ) -> Added {
        let overlays: Vec<_> = overlays
            .iter()
            .map(|(path, handler, options)| NewOverlay {
                path: path.clone(),
                handler: handler.map(str::to_owned),
                label: None,
                options: options.clone(),
            })
            .collect();
        let item = workspace::intake::video(path, &overlays, &Handlers::built_in()).unwrap();
        self.add_item(item).await
    }
}

impl Backend {
    /// A backend whose exports run on `exports`.
    pub async fn start_with_exports(exports: ExportService) -> Self {
        Self::start_with(false, Plugins::fixed(Handlers::built_in()), |state| {
            state.exports = Arc::new(exports);
        })
        .await
    }
}
