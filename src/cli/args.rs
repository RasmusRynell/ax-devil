//! The parser: the derive in `cli.rs` plus what only the app's handlers can say. `--handler-type` accepts the
//! decoders in the registry, and each resolver becomes a `playlist` subcommand whose arguments come from its settings
//! schema.
//!
//! Parsing stays pure: it returns what the user asked for, and `run` decides what to start.

use super::{Cli, Command, Global, describe};
use clap::{
    Arg, ArgAction, ArgMatches, CommandFactory, FromArgMatches, ValueHint,
    builder::PossibleValuesParser, error::ErrorKind,
};
use handlers::Handlers;
use playlist_resolvers::{Cli as CliPlacement, Field, FieldKind, PlaylistResolver, Settings};
use serde_json::Value;
use std::ffi::OsString;

/// Reads the process arguments; prints help, a version or a usage error and exits when that is what was asked for.
pub(super) fn parse(handlers: &Handlers) -> Cli {
    try_parse_from(handlers, std::env::args_os()).unwrap_or_else(|error| error.exit())
}

/// Reads `args`, whose first item is the program name.
pub(super) fn try_parse_from<I>(handlers: &Handlers, args: I) -> Result<Cli, clap::Error>
where
    I: IntoIterator,
    I::Item: Into<OsString> + Clone,
{
    let matches = command(handlers).try_get_matches_from(args)?;
    if let Some(("playlist", playlist)) = matches.subcommand() {
        // The derive knows no resolver, so it cannot read this subcommand; the options every command takes it can.
        return Ok(Cli {
            global: Global::from_arg_matches(&matches)?,
            command: Some(playlist_command_of(handlers, playlist)?),
        });
    }
    let cli = Cli::from_arg_matches(&matches)?;
    if let Some(Command::Local {
        overlay,
        handler_type,
        ..
    }) = &cli.command
        && !handler_type.is_empty()
        && handler_type.len() != overlay.len()
    {
        return Err(usage_error(
            ErrorKind::WrongNumberOfValues,
            format!(
                "--handler-type is given {} time(s) for {} --overlay; give one for every --overlay, or none",
                handler_type.len(),
                overlay.len()
            ),
        ));
    }
    Ok(cli)
}

/// The decoders and resolvers `list-handlers` prints, one per line.
pub(super) fn handlers_listing(handlers: &Handlers) -> String {
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

/// The derived command with the parts that depend on `handlers`.
fn command(handlers: &Handlers) -> clap::Command {
    Cli::command()
        .mut_subcommand("local", |local| {
            local.mut_arg("handler_type", |arg| {
                arg.value_parser(handler_parser(handlers))
            })
        })
        .subcommand(playlist_command(handlers))
}

fn playlist_command(handlers: &Handlers) -> clap::Command {
    clap::Command::new("playlist")
        .about("Open a playlist in a new Untitled workspace")
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
fn resolver_command(resolver: &dyn PlaylistResolver, handlers: &Handlers) -> clap::Command {
    let spec = resolver.spec();
    clap::Command::new(spec.id).about(spec.description).args(
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
        CliPlacement::Positional => arg,
        CliPlacement::Flag(name) => arg.long(name),
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

/// The `playlist` command that `matches`, the matches of the `playlist` subcommand, selected.
fn playlist_command_of(handlers: &Handlers, matches: &ArgMatches) -> Result<Command, clap::Error> {
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
    Ok(Command::Playlist {
        resolver: id.to_string(),
        settings: settings_from(resolver.as_ref(), resolver_matches)?,
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
    schema
        .validate(&settings)
        .map_err(|error| usage_error(ErrorKind::InvalidValue, describe(&error)))?;
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
