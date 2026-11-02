//! What the export tests share beyond the backend and viewer: serving lanes, reading the export's notices, and
//! whether the real Electron render page can run here.

use super::{Added, Backend, Event, Handlers, Viewer};
use api_types::playback::{
    BasisWire, ClientMessage, ExportStateWire, FilterOptionInfo, LanePolicyWire, LaneStatusWire,
    ServerMessage, StickyWire,
};
use ax_devil::exports::ExportService;
use std::{
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
                    basis: Some(BasisWire::Time),
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
        ax_devil::desktop::electron(&desktop)?;
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
    /// Like [`Backend::add_with_overlays`], with the handler each overlay names, for files whose extension several
    /// handlers read.
    pub async fn add_with_handlers(
        &self,
        path: &Path,
        overlays: &[(PathBuf, Option<&str>)],
    ) -> Added {
        let overlays: Vec<_> = overlays
            .iter()
            .map(|(path, handler)| NewOverlay {
                path: path.clone(),
                handler: handler.map(str::to_owned),
                label: None,
            })
            .collect();
        let item = workspace::intake::video(path, &overlays, &Handlers::built_in()).unwrap();
        self.add_item(item).await
    }
}

impl Backend {
    /// A backend whose exports run on `exports`.
    pub async fn start_with_exports(exports: ExportService) -> Self {
        Self::start_with(false, Handlers::built_in(), |state| {
            state.exports = Arc::new(exports);
        })
        .await
    }
}
