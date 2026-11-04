//! The commands that only print, `list-handlers` and `catalog`: what they print. They need no server or log file.

use super::args;
use api_types::{handlers::PluginsInfo, playback::MATCH_EXACT};
use ax_devil::{
    desktop,
    render_host::{Draw, RenderHost},
};
use catalogs::{
    CatalogId, Library, MAX_CATALOG_BYTES, Served,
    sheets::{self, Sheet},
};
use clap::{Subcommand, ValueEnum, ValueHint};
use export::{LaneStyle, Output, RenderLane, RenderSetup};
use foundation::{CancelToken, FrameSize};
use handlers::Handlers;
use media::{EncodeFormat, Encoder, Picture};
use overlays::SampleItem;
mod manage;
pub use manage::Home;
use std::{
    ffi::OsString,
    fmt::Write,
    fs,
    path::{Path, PathBuf},
    process::{Command as Process, ExitCode},
    sync::Arc,
};

/// What `catalog` was asked to do.
#[derive(Subcommand)]
pub enum CatalogCommand {
    /// List the built-in catalogs and the files of the user's catalog folder; * marks the default for new lanes.
    List,
    /// Validate a catalog and draw the example sheets with it; every problem is listed with its JSON location.
    ///
    /// Without a catalog, the default for new lanes is checked, even when it is broken.
    Check {
        /// A catalog file, `built-in:<name>`, or a catalog of the user's folder as `user:<file name>`.
        #[arg(value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        file: Option<PathBuf>,
    },
    /// Draw a sheet of example objects with a catalog and write it as a PNG; needs no display.
    ///
    /// The catalog is checked first, and a catalog with problems is listed as `check` does.
    Render {
        /// The catalog file, or a catalog of the user's folder as `user:<file name>`.
        #[arg(value_name = "FILE", value_hint = ValueHint::FilePath)]
        file: PathBuf,
        /// The folder to write the picture to; made when missing. The file is named after the sheet.
        #[arg(long, value_name = "DIR", value_hint = ValueHint::DirPath)]
        out: PathBuf,
        /// The sheet to draw.
        #[arg(long, value_enum, default_value_t = SheetName::Overview)]
        sheet: SheetName,
    },
    /// Save a copy of a catalog in the user's folder as NAME and print its file; the default does not change.
    ///
    /// A catalog that is not built in is checked first. A catalog file from anywhere is copied in.
    New {
        /// The name of the new catalog.
        name: String,
        /// The catalog to copy: `built-in:<name>`, `user:<file name>`, or a catalog file.
        #[arg(long = "from", value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        from: Option<PathBuf>,
    },
    /// Make a catalog the default for new lanes: `built-in:<name>`, `user:<file name>`, or a file in the catalog folder.
    ///
    /// A catalog that is not built in is checked first; it must be one the folder lists. A running app applies it
    /// at its next start, and a settings save before then overwrites it.
    Use {
        /// The catalog.
        #[arg(value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        catalog: PathBuf,
    },
}

/// The example sheets `catalog render` can draw.
#[derive(Clone, Copy, ValueEnum)]
pub enum SheetName {
    /// One object for every class the built-in decoders name, and a relation.
    Overview,
}

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

/// What a `catalog` subcommand prints. `home` is the user's catalog folder and settings, or why they are not known; a
/// command that names a catalog file by its path works without them.
pub fn run(command: &CatalogCommand, home: Result<Home, String>) -> Report {
    let outcome = match command {
        CatalogCommand::List => home.map(|home| manage::list(&home)),
        CatalogCommand::Check { file } => check_command(file.as_deref(), home),
        CatalogCommand::Render { file, out, sheet } => {
            checked_file(file, &home).and_then(|path| render(&path, out, *sheet))
        }
        CatalogCommand::New { name, from } => {
            home.and_then(|home| manage::new(&home, name, from.as_deref()))
        }
        CatalogCommand::Use { catalog } => {
            home.and_then(|home| manage::use_catalog(&home, catalog))
        }
    };
    outcome.unwrap_or_else(|reason| Report {
        text: format!("{reason}\n"),
        ok: false,
    })
}

/// A catalog to check: one built into the app, or a file.
pub enum Target {
    /// The built-in catalog of this name, checked from the document embedded in the app.
    BuiltIn(&'static str),
    /// A catalog file.
    File(PathBuf),
}

/// What `file` names: a built-in as `built-in:<name>`, a catalog of the user's folder as `user:<file name>`, else
/// the file at that path.
fn target_of(file: &Path, home: &Result<Home, String>) -> Result<Target, String> {
    match file.to_str().and_then(CatalogId::parse) {
        Some(CatalogId::BuiltIn(name)) => Ok(Target::BuiltIn(name)),
        Some(id) => home
            .as_ref()
            .map(|home| home.target(id))
            .map_err(Clone::clone),
        None => Ok(Target::File(file.to_path_buf())),
    }
}

/// The file `catalog render` reads, as [`target_of`] names it.
fn checked_file(file: &Path, home: &Result<Home, String>) -> Result<PathBuf, String> {
    match target_of(file, home)? {
        Target::File(path) => Ok(path),
        Target::BuiltIn(_) => Err(format!(
            "{} is built in; render a catalog file",
            file.display()
        )),
    }
}

/// `catalog check [FILE]`: the catalog `file` names, or the default for new lanes, even when that one is broken. A
/// catalog of the user's folder is checked only if the library serves it.
fn check_command(file: Option<&Path>, home: Result<Home, String>) -> Result<Report, String> {
    let (id, target) = match file {
        Some(file) => (
            file.to_str().and_then(CatalogId::parse),
            target_of(file, &home)?,
        ),
        None => {
            let home = home.as_ref().map_err(Clone::clone)?;
            let id = home.default_id(&Library::with_folder(home.folder.clone()))?;
            let target = home.target(id.clone());
            (Some(id), target)
        }
    };
    match (id, &home) {
        (Some(id @ CatalogId::User(_)), Ok(home)) => {
            manage::check_listed(home, &Library::with_folder(home.folder.clone()), &id)
        }
        _ => check_target(&target),
    }
}

/// What the catalog says about itself, or every problem in it with its location.
pub fn check_target(target: &Target) -> Result<Report, String> {
    let desktop = desktop::folder().map_err(|error| error.to_string())?;
    check_target_in(&desktop, target)
}

/// [`check_target`] with the desktop folder `desktop`. A built-in is written to a file for the checker, which names
/// the catalog by its id in what it prints.
pub fn check_target_in(desktop: &Path, target: &Target) -> Result<Report, String> {
    match target {
        Target::File(path) => Ok(check_in(desktop, path)?.report()),
        Target::BuiltIn(name) => {
            let id = CatalogId::BuiltIn(name);
            let Served::Document { text, .. } = Library::built_in().document(&id) else {
                return Err(format!("{id} is not a catalog of this app"));
            };
            let scratch = tempfile::tempdir()
                .map_err(|error| format!("Cannot make a folder to check {id} in: {error}"))?;
            let file = scratch.path().join(format!("{name}.json"));
            fs::write(&file, text.as_bytes())
                .map_err(|error| format!("Cannot write {}: {error}", file.display()))?;
            let mut report = check_in(desktop, &file)?.report();
            report.text = report
                .text
                .replace(&file.display().to_string(), &id.to_string());
            Ok(report)
        }
    }
}

/// What the frontend's checker made of a catalog: whether it compiles, and whether the example sheets draw.
pub enum Checked {
    /// The catalog compiles and every sheet draws; the checker's report.
    Good(String),
    /// The catalog does not compile: the checker's list of its problems.
    CannotCompile(String),
    /// The catalog compiles but drawing the sheets with it failed.
    SheetsFailed {
        /// The checker's report.
        text: String,
        /// The problems drawing the sheets, one per line, with their locations.
        problems: Vec<String>,
    },
}

impl Checked {
    /// The checker's report; a catalog with either kind of problem failed.
    fn report(self) -> Report {
        match self {
            Self::Good(text) => Report { text, ok: true },
            Self::CannotCompile(text) | Self::SheetsFailed { text, .. } => {
                Report { text, ok: false }
            }
        }
    }
}

/// Checks the catalog `path` in the desktop folder `desktop`: the compiler and every example sheet to draw it on. A
/// catalog that fails is checked again without the sheets, to tell a catalog that does not compile from one that
/// does not draw.
pub fn check_in(desktop: &Path, path: &Path) -> Result<Checked, String> {
    let scratch = tempfile::tempdir()
        .map_err(|error| format!("Cannot make a folder for the example sheets: {error}"))?;
    let mut sheets = Vec::new();
    for sheet in SheetName::value_variants() {
        let file = scratch.path().join(sheet.stem());
        fs::write(&file, sheet.build()?.item)
            .map_err(|error| format!("Cannot write {}: {error}", file.display()))?;
        let mut argument = OsString::from(format!("{}=", sheet.stem()));
        argument.push(file);
        sheets.push(argument);
    }
    let drawn = run_checker(desktop, path, &sheets)?;
    if drawn.ok {
        return Ok(Checked::Good(drawn.text));
    }
    let compiled = run_checker(desktop, path, &[])?;
    if !compiled.ok {
        return Ok(Checked::CannotCompile(compiled.text));
    }
    // The first line of the report names the kind of problem; the rest are the problems.
    let problems = drawn
        .text
        .lines()
        .skip(1)
        .map(|line| line.trim().to_owned())
        .filter(|line| !line.is_empty())
        .collect();
    Ok(Checked::SheetsFailed {
        text: drawn.text,
        problems,
    })
}

/// Runs the frontend's catalog checker on `path` in Electron-as-Node with the `NAME=PATH` sheets. The checker reads
/// the files and writes the report; this only starts it.
fn run_checker(desktop: &Path, path: &Path, sheets: &[OsString]) -> Result<Report, String> {
    let electron = desktop::electron(desktop)?;
    let checker = desktop::catalog_checker(desktop)?;
    let output = Process::new(electron)
        .env("ELECTRON_RUN_AS_NODE", "1")
        .arg(checker)
        .arg(path)
        .args(sheets)
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

/// `catalog render FILE --out DIR`: the catalog's problems as `check` lists them when it does not compile, else the
/// sheet drawn with it and written to `out`, with the problems of drawing the sheets listed after it. Needs the desktop folder, whose Electron compiles and draws the catalog.
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
    let drawing_errors = match check_in(desktop, path)? {
        Checked::Good(_) => Vec::new(),
        Checked::SheetsFailed { problems, .. } => problems,
        failed @ Checked::CannotCompile(_) => return Ok(failed.report()),
    };
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
    let mut text = format!("Wrote {}\n", written.display());
    for problem in drawing_errors {
        let _ = writeln!(text, "  drawing error: {problem}");
    }
    Ok(Report { text, ok: true })
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
