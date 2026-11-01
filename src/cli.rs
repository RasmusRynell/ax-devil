//! The command line, built from what the app knows: the `playlist` subcommands and their arguments come from each
//! resolver's settings schema, and `--handler-type` accepts the decoders in the registry.
//!
//! Parsing stays pure: it returns what the user asked for, and `main` decides what to start. Commands that only
//! print, such as `list-handlers`, never need the server, the settings file or the log file.

use clap::{
    Arg, ArgAction, ArgMatches, Command, ValueHint, builder::PossibleValuesParser,
    error::ErrorKind, value_parser,
};
use handlers::Handlers;
use playlist_resolvers::{Cli, Field, FieldKind, PlaylistResolver, Settings};
use serde_json::Value;
use std::{ffi::OsString, path::PathBuf};
use workspace::{WorkspaceItem, intake::NewOverlay};

mod catalog;

/// Everything one run of the program was asked to do.
pub struct Invocation {
    /// The settings file given with `--config`.
    pub config: Option<PathBuf>,
    /// The cache folder given with `--cache-dir`.
    pub cache_dir: Option<PathBuf>,
    /// How much to log.
    pub log_level: logging::Level,
    /// What to run.
    pub action: Action,
}

/// What to run.
pub enum Action {
    /// Print the decoders and resolvers and exit; needs no server.
    ListHandlers,
    /// List the catalogs or check a catalog file and exit; needs no server.
    Catalog(CatalogCommand),
    /// Start the app, with the item the command line asked for, if any.
    Serve(Opens),
}

/// What `catalog` was asked to do.
pub enum CatalogCommand {
    /// Print the built-in catalogs.
    List,
    /// Validate a catalog file and print what it says about itself, or its problems.
    Check(PathBuf),
}

/// The one item a command line adds to the new Untitled workspace.
pub enum Opens {
    /// Nothing: the plain launch.
    Nothing,
    /// `local`: a video with overlays.
    Video {
        /// The video file.
        video: PathBuf,
        /// The overlay files, each with its decoder when the user named it.
        overlays: Vec<NewOverlay>,
    },
    /// `playlist`: a resolver and the settings its arguments gave.
    Playlist {
        /// The resolver id.
        resolver: String,
        /// The settings, keyed as the resolver's schema declares.
        settings: Settings,
    },
}

impl Opens {
    /// The items to add: none, or the one the command line described, built by the same intake as the dialogs.
    pub fn into_items(
        self,
        handlers: &Handlers,
    ) -> Result<Vec<WorkspaceItem>, foundation::UserError> {
        match self {
            Self::Nothing => Ok(Vec::new()),
            Self::Video { video, overlays } => {
                workspace::intake::video(&video, &overlays, handlers).map(|item| vec![item])
            }
            Self::Playlist { resolver, settings } => {
                workspace::intake::playlist(&resolver, settings, None, handlers)
                    .map(|item| vec![item])
            }
        }
    }
}

/// What a `catalog` subcommand prints, and whether it succeeded.
pub fn run_catalog(command: &CatalogCommand) -> catalog::Report {
    match command {
        CatalogCommand::List => catalog::list(&catalogs::Library::built_in()),
        CatalogCommand::Check(path) => catalog::check(path),
    }
}

/// Reads the process arguments; prints help, a version or a usage error and exits when that is what was asked for.
pub fn parse(handlers: &Handlers) -> Invocation {
    try_parse_from(handlers, std::env::args_os()).unwrap_or_else(|error| error.exit())
}

/// Reads `args`, whose first item is the program name.
pub fn try_parse_from<I>(handlers: &Handlers, args: I) -> Result<Invocation, clap::Error>
where
    I: IntoIterator,
    I::Item: Into<OsString> + Clone,
{
    let matches = command(handlers).try_get_matches_from(args)?;
    Ok(Invocation {
        config: matches.get_one::<PathBuf>("config").cloned(),
        cache_dir: matches.get_one::<PathBuf>("cache-dir").cloned(),
        log_level: matches
            .get_one::<logging::Level>("log-level")
            .copied()
            .unwrap_or_default(),
        action: action(handlers, &matches)?,
    })
}

/// The decoders and resolvers `list-handlers` prints, one per line.
pub fn handlers_listing(handlers: &Handlers) -> String {
    let decoders: Vec<_> = handlers
        .file_decoders()
        .iter()
        .map(|decoder| {
            let spec = decoder.spec();
            (
                spec.handler,
                format!("{} ({})", spec.display_name, spec.extensions.join(", ")),
            )
        })
        .collect();
    let resolvers: Vec<_> = handlers
        .resolvers()
        .iter()
        .map(|resolver| {
            let spec = resolver.spec();
            (
                spec.id,
                format!("{}: {}", spec.display_name, spec.description),
            )
        })
        .collect();
    let width = decoders
        .iter()
        .chain(&resolvers)
        .map(|(key, _)| key.len())
        .max()
        .unwrap_or(0);
    let mut listing = String::from("File overlay handlers (for --handler-type):\n");
    for (key, line) in &decoders {
        listing.push_str(&format!("  {key:<width$}  {line}\n"));
    }
    listing.push_str("\nPlaylist resolvers (ax-devil playlist <resolver> --help):\n");
    for (key, line) in &resolvers {
        listing.push_str(&format!("  {key:<width$}  {line}\n"));
    }
    listing
}

fn command(handlers: &Handlers) -> Command {
    Command::new("ax-devil")
        .version(env!("CARGO_PKG_VERSION"))
        .about("Desktop app for inspecting Axis camera streams, offline video, and analytics overlays.")
        .arg(
            Arg::new("config")
                .long("config")
                .value_name("PATH")
                .value_parser(value_parser!(PathBuf))
                .value_hint(ValueHint::FilePath)
                .help("Use this settings file instead of $XDG_CONFIG_HOME/ax-devil/settings.json (~/.config/ax-devil/settings.json)."),
        )
        .arg(
            Arg::new("cache-dir")
                .long("cache-dir")
                .value_name("PATH")
                .value_parser(value_parser!(PathBuf))
                .value_hint(ValueHint::DirPath)
                .help("Keep caches in this folder instead of $XDG_CACHE_HOME/ax-devil (~/.cache/ax-devil)."),
        )
        .arg(
            Arg::new("log-level")
                .long("log-level")
                .value_name("DEBUG|INFO|WARNING|ERROR")
                .default_value("INFO")
                .value_parser(value_parser!(logging::Level))
                .help("How much to log."),
        )
        .subcommand(local_command(handlers))
        .subcommand(playlist_command(handlers))
        .subcommand(
            Command::new("list-handlers")
                .about("List the overlay decoders and playlist resolvers, then exit."),
        )
        .subcommand(catalog_command())
}

fn catalog_command() -> Command {
    Command::new("catalog")
        .about("List the render catalogs or check a catalog file, then exit.")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(Command::new("list").about("List the built-in catalogs."))
        .subcommand(
            Command::new("check")
                .about("Validate a catalog file; every problem is listed with its JSON location.")
                .arg(
                    Arg::new("file")
                        .value_name("FILE")
                        .required(true)
                        .value_parser(value_parser!(PathBuf))
                        .value_hint(ValueHint::FilePath)
                        .help("The catalog file."),
                ),
        )
}

fn local_command(handlers: &Handlers) -> Command {
    Command::new("local")
        .about("Open an offline video file in a new Untitled workspace.")
        .long_about(
            "Open an offline video file in a new Untitled workspace.\n\nA file that cannot be read stays listed in the workspace, with the reason.",
        )
        .arg(
            Arg::new("video")
                .long("video")
                .value_name("PATH")
                .required(true)
                .value_parser(value_parser!(PathBuf))
                .value_hint(ValueHint::FilePath)
                .help("The video file."),
        )
        .arg(
            Arg::new("overlay")
                .long("overlay")
                .value_name("PATH")
                .action(ArgAction::Append)
                .value_parser(value_parser!(PathBuf))
                .value_hint(ValueHint::FilePath)
                .help("An overlay file drawn on the video; repeat for several."),
        )
        .arg(
            Arg::new("handler-type")
                .long("handler-type")
                .value_name("HANDLER")
                .action(ArgAction::Append)
                .value_parser(handler_parser(handlers))
                .help("The decoder of the --overlay in the same position. Give one for every --overlay or none, which picks each decoder by file extension."),
        )
}

fn playlist_command(handlers: &Handlers) -> Command {
    Command::new("playlist")
        .about("Open a playlist in a new Untitled workspace.")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommands(
            handlers
                .resolvers()
                .iter()
                .map(|resolver| resolver_command(resolver.as_ref(), handlers)),
        )
}

/// One resolver's subcommand: an argument per schema field.
fn resolver_command(resolver: &dyn PlaylistResolver, handlers: &Handlers) -> Command {
    let spec = resolver.spec();
    Command::new(spec.id).about(spec.description).args(
        resolver
            .schema()
            .fields
            .iter()
            .map(|field| field_arg(field, handlers)),
    )
}

fn field_arg(field: &Field, handlers: &Handlers) -> Arg {
    let arg = Arg::new(field.key).required(field.required);
    let arg = match field.cli {
        Cli::Positional => arg,
        Cli::Flag(name) => arg.long(name),
    };
    match &field.kind {
        FieldKind::Folder => arg.value_hint(ValueHint::DirPath).help(field.label),
        FieldKind::File { .. } => arg.value_hint(ValueHint::FilePath).help(field.label),
        FieldKind::Text => arg.help(field.label),
        FieldKind::Handler => arg
            .value_name("HANDLER")
            .value_parser(handler_parser(handlers))
            .help(field.label),
        FieldKind::Scan { .. } => arg
            .value_name("A,B,...")
            .value_delimiter(',')
            .action(ArgAction::Append)
            .help(format!(
                "{}, comma-separated; all when left out",
                field.label
            )),
    }
}

fn handler_parser(handlers: &Handlers) -> PossibleValuesParser {
    PossibleValuesParser::new(
        handlers
            .file_decoders()
            .iter()
            .map(|decoder| decoder.spec().handler),
    )
}

fn action(handlers: &Handlers, matches: &ArgMatches) -> Result<Action, clap::Error> {
    match matches.subcommand() {
        None => Ok(Action::Serve(Opens::Nothing)),
        Some(("list-handlers", _)) => Ok(Action::ListHandlers),
        Some(("catalog", catalog)) => Ok(Action::Catalog(match catalog.subcommand() {
            Some(("check", check)) => CatalogCommand::Check(
                check
                    .get_one::<PathBuf>("file")
                    .cloned()
                    .unwrap_or_default(),
            ),
            _ => CatalogCommand::List,
        })),
        Some(("local", local)) => local_opens(local).map(Action::Serve),
        Some(("playlist", playlist)) => playlist_opens(handlers, playlist).map(Action::Serve),
        Some((name, _)) => Err(usage_error(
            ErrorKind::InvalidSubcommand,
            format!("unrecognized subcommand '{name}'"),
        )),
    }
}

fn local_opens(matches: &ArgMatches) -> Result<Opens, clap::Error> {
    let paths: Vec<PathBuf> = matches
        .get_many("overlay")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    let handler_types: Vec<&String> = matches
        .get_many("handler-type")
        .into_iter()
        .flatten()
        .collect();
    if !handler_types.is_empty() && handler_types.len() != paths.len() {
        return Err(usage_error(
            ErrorKind::WrongNumberOfValues,
            format!(
                "--handler-type is given {} time(s) for {} --overlay; give one for every --overlay, or none",
                handler_types.len(),
                paths.len()
            ),
        ));
    }
    let overlays = paths
        .into_iter()
        .enumerate()
        .map(|(position, path)| NewOverlay {
            path,
            handler: handler_types
                .get(position)
                .map(|handler| (*handler).clone()),
        })
        .collect();
    Ok(Opens::Video {
        video: matches
            .get_one::<PathBuf>("video")
            .cloned()
            .unwrap_or_default(),
        overlays,
    })
}

fn playlist_opens(handlers: &Handlers, matches: &ArgMatches) -> Result<Opens, clap::Error> {
    let Some((id, resolver_matches)) = matches.subcommand() else {
        return Err(usage_error(
            ErrorKind::MissingSubcommand,
            "name a playlist resolver".to_string(),
        ));
    };
    let Some(resolver) = handlers.resolver(id) else {
        return Err(usage_error(
            ErrorKind::InvalidSubcommand,
            format!("unrecognized playlist resolver '{id}'"),
        ));
    };
    let settings = settings_from(resolver.as_ref(), resolver_matches)?;
    Ok(Opens::Playlist {
        resolver: id.to_string(),
        settings,
    })
}

/// The settings the arguments gave: folders and files made absolute, a `Scan` field a list when given and left out
/// when not (which means all of them).
fn settings_from(
    resolver: &dyn PlaylistResolver,
    matches: &ArgMatches,
) -> Result<Settings, clap::Error> {
    let schema = resolver.schema();
    let mut settings = Settings::new();
    for field in schema.fields {
        let value = match &field.kind {
            FieldKind::Scan { .. } => matches
                .get_many::<String>(field.key)
                .map(|names| Value::Array(names.cloned().map(Value::String).collect())),
            FieldKind::Folder | FieldKind::File { .. } => matches
                .get_one::<String>(field.key)
                .map(|path| absolute(field, path).map(Value::String))
                .transpose()?,
            FieldKind::Text | FieldKind::Handler => matches
                .get_one::<String>(field.key)
                .cloned()
                .map(Value::String),
        };
        if let Some(value) = value {
            settings.insert(field.key.to_string(), value);
        }
    }
    schema.validate(&settings).map_err(|error| {
        usage_error(
            ErrorKind::InvalidValue,
            format!("{}: {}", error.location, error.reason),
        )
    })?;
    Ok(settings)
}

/// `path` as an absolute path, so the item means the same folder whatever the working folder.
fn absolute(field: &Field, path: &str) -> Result<String, clap::Error> {
    std::path::absolute(path)
        .ok()
        .and_then(|absolute| absolute.to_str().map(str::to_owned))
        .ok_or_else(|| {
            usage_error(
                ErrorKind::InvalidValue,
                format!("{}: '{path}' is not a usable path", field.label),
            )
        })
}

fn usage_error(kind: ErrorKind, message: String) -> clap::Error {
    clap::Error::raw(kind, message)
}

#[cfg(test)]
mod tests;
