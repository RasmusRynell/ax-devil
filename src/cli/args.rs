//! The parser: the derive in `cli.rs` plus what only the app's handlers can say. `--handler-type` accepts the
//! decoders in the registry, and each resolver becomes a `playlist` subcommand whose arguments come from its settings
//! schema.
//!
//! Parsing stays pure: it returns what the user asked for, and `run` decides what to start.

use super::{Cli, Command, Global, OverlayOption, describe};
use api_types::handlers::{PluginStatus, PluginsInfo};
use clap::{
    Arg, ArgAction, ArgMatches, CommandFactory, FromArgMatches, ValueHint,
    builder::PossibleValuesParser, error::ErrorKind,
};
use foundation::UserError;
use handler_api::{
    Cli as CliPlacement, Decoder, Field, FieldKind, PlaylistResolver, Settings, invalid_field,
};
use handlers::Handlers;
use serde_json::Value;
use std::{ffi::OsString, path::Path, sync::Arc};

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
    let mut cli = cli;
    if let Some(Command::Local {
        overlay,
        handler_type,
        overlay_option,
        ..
    }) = &mut cli.command
    {
        if !handler_type.is_empty() && handler_type.len() != overlay.len() {
            return Err(usage_error(
                ErrorKind::WrongNumberOfValues,
                format!(
                    "--handler-type is given {} time(s) for {} --overlay; give one for every --overlay, or none",
                    handler_type.len(),
                    overlay.len()
                ),
            ));
        }
        if let Some(local) = matches.subcommand_matches("local") {
            attach_overlay_options(local, overlay_option)?;
        }
    }
    Ok(cli)
}

/// Gives each `--overlay-option` the position of the nearest `--overlay` before it on the command line.
fn attach_overlay_options(
    local: &ArgMatches,
    options: &mut [OverlayOption],
) -> Result<(), clap::Error> {
    let overlays: Vec<usize> = local.indices_of("overlay").into_iter().flatten().collect();
    let at = local.indices_of("overlay_option").into_iter().flatten();
    for (option, at) in options.iter_mut().zip(at) {
        let before = overlays.partition_point(|overlay| *overlay < at);
        option.overlay = before.checked_sub(1).ok_or_else(|| {
            usage_error(
                ErrorKind::ArgumentConflict,
                format!(
                    "--overlay-option {}={} comes before any --overlay",
                    option.key, option.value
                ),
            )
        })?;
    }
    Ok(())
}

/// Reads one `--overlay-option` as `KEY=VALUE`.
pub(super) fn parse_overlay_option(text: &str) -> Result<OverlayOption, String> {
    match text.split_once('=') {
        Some((key, value)) if !key.is_empty() => Ok(OverlayOption {
            overlay: 0,
            key: key.to_owned(),
            value: value.to_owned(),
        }),
        _ => Err("expected KEY=VALUE".to_owned()),
    }
}

/// The options `given` for the overlay file `path`, typed by the form of its decoder: `handler`, else the only
/// decoder for the extension. A decoder not found leaves the values text, for the intake to refuse.
pub(super) fn overlay_settings<'a>(
    handlers: &Handlers,
    path: &Path,
    handler: Option<&str>,
    given: impl Iterator<Item = &'a OverlayOption>,
) -> Result<Settings, UserError> {
    let decoder = match handler {
        Some(handler) => handlers.decoder(handler),
        None => match handlers.decoders_for(path)[..] {
            [only] => Some(only),
            _ => None,
        },
    };
    typed_options(decoder, given)
}

/// The options `given` as settings, each typed by the form of `decoder`: a whole number where it declares one.
fn typed_options<'a>(
    decoder: Option<&Arc<dyn Decoder>>,
    given: impl Iterator<Item = &'a OverlayOption>,
) -> Result<Settings, UserError> {
    let mut settings = Settings::new();
    for option in given {
        let integer = decoder.is_some_and(|decoder| {
            decoder.spec().options.fields.iter().any(|field| {
                field.key == option.key && matches!(field.kind, FieldKind::Integer { .. })
            })
        });
        let value = if integer {
            let number: i64 = option
                .value
                .parse()
                .map_err(|_| invalid_field("Must be a whole number", &option.key))?;
            Value::from(number)
        } else {
            Value::String(option.value.clone())
        };
        settings.insert(option.key.clone(), value);
    }
    Ok(settings)
}

/// The decoders and resolvers `list-handlers` prints, one per line, then the converters that did not load.
pub(super) fn handlers_listing(handlers: &Handlers, installed: &PluginsInfo) -> String {
    let decoders: Vec<_> = handlers
        .decoders()
        .iter()
        .map(|decoder| {
            let spec = decoder.spec();
            (
                spec.id.as_str(),
                format!("{} ({})", spec.name, spec.extensions.join(", ")),
            )
        })
        .collect();
    let resolvers: Vec<_> = handlers
        .resolvers()
        .iter()
        .map(|resolver| {
            let spec = resolver.spec();
            (
                spec.id.as_str(),
                format!("{}: {}", spec.name, spec.description),
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
    let problems: Vec<String> = installed
        .problem
        .iter()
        .cloned()
        .chain(
            installed
                .converters
                .iter()
                .filter_map(|plugin| match &plugin.status {
                    PluginStatus::Failed { reason } => {
                        Some(format!("{}: {reason}", plugin.manifest))
                    }
                    PluginStatus::Loaded { .. } => None,
                }),
        )
        .collect();
    if !problems.is_empty() {
        listing.push_str("\nConverters that did not load (ax-devil plugins list):\n");
        for problem in problems {
            listing.push_str(&format!("  {problem}\n"));
        }
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
    clap::Command::new(spec.id.clone())
        .about(spec.description.clone())
        .args(
            spec.form
                .fields
                .iter()
                .map(|field| field_arg(field, handlers)),
        )
}

fn field_arg(field: &Field, handlers: &Handlers) -> Arg {
    let arg = Arg::new(field.key.clone()).required(field.required);
    let arg = match &field.cli {
        CliPlacement::Positional => arg,
        CliPlacement::Flag(name) => arg.long(name.clone()),
    };
    let label = field.label.clone();
    match &field.kind {
        FieldKind::Folder => arg.value_hint(ValueHint::DirPath).help(label),
        FieldKind::File { .. } => arg.value_hint(ValueHint::FilePath).help(label),
        FieldKind::Text => arg.help(label),
        FieldKind::Integer { .. } => arg
            .value_name("N")
            .value_parser(clap::value_parser!(i64))
            .help(label),
        FieldKind::Handler => arg
            .value_name("HANDLER")
            .value_parser(handler_parser(handlers))
            .help(label),
        FieldKind::DecoderOptions { .. } => arg
            .value_name("KEY=VALUE")
            .value_parser(parse_overlay_option)
            .action(ArgAction::Append)
            .help(label),
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
            .decoders()
            .iter()
            .map(|decoder| decoder.spec().id.clone()),
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
        settings: settings_from(handlers, resolver.as_ref(), resolver_matches)?,
    })
}

/// The settings the arguments gave: folders and files made absolute, a `Scan` field a list when given and left out
/// when not (which means all of them), and decoder options typed by the chosen decoder.
fn settings_from(
    handlers: &Handlers,
    resolver: &dyn PlaylistResolver,
    matches: &ArgMatches,
) -> Result<Settings, clap::Error> {
    let form = &resolver.spec().form;
    let mut settings = Settings::new();
    for field in &form.fields {
        let key = field.key.as_str();
        let value = match &field.kind {
            FieldKind::Scan { .. } => matches
                .get_many::<String>(key)
                .map(|names| Value::Array(names.cloned().map(Value::String).collect())),
            FieldKind::Folder | FieldKind::File { .. } => matches
                .get_one::<String>(key)
                .map(|path| absolute(field, path).map(Value::String))
                .transpose()?,
            FieldKind::Integer { .. } => matches
                .get_one::<i64>(key)
                .map(|number| Value::from(*number)),
            FieldKind::DecoderOptions { handler_field } => {
                let decoder = matches
                    .get_one::<String>(handler_field)
                    .and_then(|id| handlers.decoder(id));
                let given = matches.get_many::<OverlayOption>(key).into_iter().flatten();
                let options = typed_options(decoder, given)
                    .map_err(|error| usage_error(ErrorKind::InvalidValue, describe(&error)))?;
                (!options.is_empty()).then_some(Value::Object(options))
            }
            FieldKind::Text | FieldKind::Handler => {
                matches.get_one::<String>(key).cloned().map(Value::String)
            }
        };
        if let Some(value) = value {
            settings.insert(field.key.clone(), value);
        }
    }
    form.validate(&settings)
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
