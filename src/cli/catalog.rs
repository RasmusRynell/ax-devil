//! The commands that only print, `list-handlers` and `catalog`: what they print. They need no server, settings or
//! log file.

use super::{CatalogCommand, SheetName, args};
use api_types::{handlers::PluginsInfo, playback::MATCH_EXACT};
use ax_devil::{
    desktop,
    render_host::{Draw, RenderHost},
};
use catalogs::{
    CatalogId, Library, MAX_CATALOG_BYTES,
    sheets::{self, Sheet},
};
use export::{LaneStyle, Output, RenderLane, RenderSetup};
use foundation::{CancelToken, FrameSize};
use handlers::Handlers;
use media::{EncodeFormat, Encoder, Picture};
use overlays::SampleItem;
use std::{
    fmt::Write,
    fs,
    path::{Path, PathBuf},
    process::{Command as Process, ExitCode},
    sync::Arc,
};

/// The largest picture a sheet may be drawn at.
const MAX_SHEET: FrameSize = FrameSize {
    width: 3840,
    height: 2160,
};

/// What a `catalog` subcommand prints and whether it succeeded.
#[derive(Debug)]
pub struct Report {
    /// The text to print.
    pub text: String,
    /// Whether the command succeeded; a failed one prints to the error output and exits with a failure.
    pub ok: bool,
}

impl Report {
    /// `list-handlers`: the decoders and resolvers `handlers` holds, and the plugins that did not load.
    pub fn listing(handlers: &Handlers, installed: &PluginsInfo) -> Self {
        Self {
            text: args::handlers_listing(handlers, installed),
            ok: true,
        }
    }

    /// Prints the report, to the error output when it failed, and gives the exit code.
    #[allow(clippy::print_stdout, clippy::print_stderr)]
    pub fn print(self) -> ExitCode {
        if self.ok {
            print!("{}", self.text);
            ExitCode::SUCCESS
        } else {
            eprint!("{}", self.text);
            ExitCode::FAILURE
        }
    }
}

/// What a `catalog` subcommand prints. `folder` is the user's catalog folder, or why it is not known; a command
/// that does not read it works without it.
pub fn run(command: &CatalogCommand, folder: Result<PathBuf, String>) -> Report {
    let failed = |reason: String| Report {
        text: format!("{reason}\n"),
        ok: false,
    };
    match command {
        CatalogCommand::List => match folder {
            Ok(folder) => list(&Library::with_folder(folder)),
            Err(reason) => failed(reason),
        },
        CatalogCommand::Check { file } => match checked_file(file, folder) {
            Ok(path) => check(&path),
            Err(reason) => failed(reason),
        },
        CatalogCommand::Render { file, out, sheet } => {
            match checked_file(file, folder).and_then(|path| render(&path, out, *sheet)) {
                Ok(report) => report,
                Err(reason) => failed(reason),
            }
        }
    }
}

/// The file `catalog check` reads: `file` itself, or the file of the user's folder that `user:<file name>` names.
pub fn checked_file(file: &Path, folder: Result<PathBuf, String>) -> Result<PathBuf, String> {
    match file.to_str().and_then(CatalogId::parse) {
        Some(CatalogId::User(name)) => folder.map(|folder| folder.join(name)),
        _ => Ok(file.to_path_buf()),
    }
}

/// `catalog list`: the catalogs the app serves, one per line.
pub fn list(library: &Library) -> Report {
    let infos = library.documents();
    let width = infos
        .iter()
        .map(|info| info.id.to_string().len())
        .max()
        .unwrap_or(0);
    let mut text = String::new();
    for info in &infos {
        let _ = writeln!(
            text,
            "{:<width$}  {}: {}",
            info.id.to_string(),
            info.name,
            info.description
        );
    }
    Report { text, ok: true }
}

/// `catalog check FILE`: what the catalog says about itself, or every problem in it with its location.
pub fn check(path: &Path) -> Report {
    match desktop::folder()
        .map_err(|error| error.to_string())
        .and_then(|desktop| check_in(&desktop, path))
    {
        Ok(report) => report,
        Err(reason) => Report {
            text: format!("{reason}\n"),
            ok: false,
        },
    }
}

/// Runs the frontend's catalog compiler on `path` in Electron-as-Node, from the desktop folder `desktop`. The
/// compiler reads the file and writes the report; this only starts it.
pub fn check_in(desktop: &Path, path: &Path) -> Result<Report, String> {
    let electron = desktop::electron(desktop)?;
    let checker = desktop::catalog_checker(desktop)?;
    let output = Process::new(electron)
        .env("ELECTRON_RUN_AS_NODE", "1")
        .arg(checker)
        .arg(path)
        .output()
        .map_err(|error| format!("Cannot start Electron to check the catalog: {error}"))?;
    let mut text = String::from_utf8_lossy(&output.stdout).into_owned();
    if text.is_empty() {
        text = String::from_utf8_lossy(&output.stderr).into_owned();
    }
    Ok(Report {
        text,
        ok: output.status.success(),
    })
}

impl SheetName {
    /// The name of the sheet's picture file, without the extension.
    fn stem(self) -> &'static str {
        match self {
            Self::Overview => "overview",
        }
    }

    fn build(self) -> Result<Sheet, String> {
        match self {
            Self::Overview => sheets::overview(),
        }
    }
}

/// `catalog render FILE --out DIR`: the catalog's problems as `check` lists them, or the sheet drawn with it and
/// written to `out`. Needs the desktop folder, whose Electron compiles and draws the catalog.
pub fn render(path: &Path, out: &Path, sheet: SheetName) -> Result<Report, String> {
    let desktop = desktop::folder().map_err(|error| error.to_string())?;
    render_in(&desktop, path, out, sheet)
}

/// [`render`] with the desktop folder `desktop`.
pub fn render_in(
    desktop: &Path,
    path: &Path,
    out: &Path,
    sheet: SheetName,
) -> Result<Report, String> {
    let checked = check_in(desktop, path)?;
    if !checked.ok {
        return Ok(checked);
    }
    let catalog = read_catalog(path)?;
    let sheet_data = sheet.build()?;
    let mut host =
        RenderHost::start(desktop, &CancelToken::new()).map_err(|error| error.to_string())?;
    let picture = sheet_data.picture.layout();
    let size = host
        .setup(RenderSetup {
            output: Output::Rgba,
            picture: FrameSize {
                width: picture.width,
                height: picture.height,
            },
            max_output: MAX_SHEET,
            lanes: vec![RenderLane {
                source: Some(0),
                catalog,
                style: LaneStyle {
                    label: sheet.stem().to_owned(),
                    enabled_classes: Vec::new(),
                    class_options: 0,
                    hidden: 0,
                    theme: None,
                    retained_opacity: 1.0,
                    filter: None,
                },
            }],
        })
        .map_err(|error| error.to_string())?;
    let item = SampleItem {
        source: 0,
        data_revision: 0,
        sample: 0,
        time: None,
        frame_number: None,
        frame: 0,
        serial: 0,
        item: Arc::from(sheet_data.item),
    };
    host.submit(&Draw {
        seq: 0,
        picture: &sheet_data.picture,
        items: &[item],
        cells: &[0],
        kinds: &[MATCH_EXACT],
    })
    .map_err(|error| error.to_string())?;
    let pixels = host.receive().map_err(|error| error.to_string())?;
    let written = write_png(
        out,
        sheet.stem(),
        size,
        &pixels.data,
        pixels.stride0 as usize,
    )?;
    Ok(Report {
        text: format!("Wrote {}\n", written.display()),
        ok: true,
    })
}

/// The catalog text of `path`, refused when it is larger than the viewer would take.
fn read_catalog(path: &Path) -> Result<String, String> {
    let cannot = |error: &dyn std::fmt::Display| format!("Cannot read {}: {error}", path.display());
    let file = fs::File::open(path).map_err(|error| cannot(&error))?;
    if file.metadata().map_err(|error| cannot(&error))?.len() > MAX_CATALOG_BYTES {
        return Err(cannot(&format!(
            "it is larger than {MAX_CATALOG_BYTES} bytes"
        )));
    }
    fs::read_to_string(path).map_err(|error| cannot(&error))
}

/// Writes the RGBA rows as `<stem>.png` in `out`; the encoder writes it through a new sibling file, so a failure
/// leaves nothing half written and a link or another run's file at a fixed name is never touched.
fn write_png(
    out: &Path,
    stem: &str,
    size: FrameSize,
    rgba: &[u8],
    stride: usize,
) -> Result<PathBuf, String> {
    fs::create_dir_all(out).map_err(|error| format!("Cannot create {}: {error}", out.display()))?;
    let png = out.join(format!("{stem}.png"));
    let mut encoder = Encoder::create(&png, EncodeFormat::Png, size.width, size.height)
        .map_err(|error| error.reason)?;
    encoder
        .write(Picture::Rgba { data: rgba, stride }, 0, 1)
        .and_then(|()| encoder.finish())
        .map_err(|error| error.reason)?;
    Ok(png)
}
