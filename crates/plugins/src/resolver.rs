//! A plugin's playlist resolver handler as a [`PlaylistResolver`]: the resolver `playlist <id>` and workspaces name.

use std::{path::PathBuf, sync::Arc};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, Location, UserError};
use handler_api::{
    Choice, FieldKind, PlaylistResolver, ResolvedPlaylists, ResolverSpec, Values, invalid_field,
};
use serde_json::json;

use crate::{
    PROTOCOL,
    manifest::ResolverHandler,
    program::Program,
    resolved::{Collected, Op},
    run::End,
};

/// A resolver that runs an installed plugin's process: once to scan a setting's choices, once to resolve.
pub struct PluginResolver {
    program: Arc<Program>,
    spec: ResolverSpec,
    watches: bool,
}

impl PluginResolver {
    /// The resolver for `handler`, which `program`'s plugin declares.
    pub fn new(program: Arc<Program>, handler: &ResolverHandler) -> Self {
        let spec = ResolverSpec {
            id: handler.id.clone(),
            plugin: program.manifest().id.clone(),
            name: handler.name.clone(),
            description: handler.description.clone(),
            form: handler.settings.clone(),
        };
        Self {
            program,
            spec,
            watches: handler.watches,
        }
    }

    /// Runs the plugin with `request` and returns what its lines added up to once it exited 0. A reported `error`
    /// becomes a `UserError` about its field or path; any other failure carries the plugin's stderr tail.
    fn call(
        &self,
        op: Op,
        request: &serde_json::Value,
        cancel: &CancelToken,
    ) -> Result<Collected, Interrupt<UserError>> {
        cancel.check()?;
        let mut collected = Collected::new(op);
        let end = self
            .program
            .run(&self.spec.id, request, &mut collected, cancel);
        if let Some(reported) = collected.error.take() {
            let error = UserError::new(ErrorCode::PlaylistInvalid, reported.reason);
            return Err(Interrupt::Failed(match (reported.field, reported.path) {
                (Some(field), _) => error.at(Location::Field(field)),
                (None, Some(path)) => error.at(HostPath::new(path)),
                (None, None) => error,
            }));
        }
        match end {
            End::Complete => Ok(collected),
            End::Incomplete(reason) => Err(Interrupt::Failed(UserError::new(
                ErrorCode::PlaylistInvalid,
                reason,
            ))),
            End::Stopped => Err(Interrupt::Cancelled),
        }
    }
}

impl PlaylistResolver for PluginResolver {
    fn spec(&self) -> &ResolverSpec {
        &self.spec
    }

    fn scan(
        &self,
        field: &str,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        let scans = self.spec.form.fields.iter().any(|declared| {
            declared.key == field && matches!(declared.kind, FieldKind::Scan { .. })
        });
        if !scans {
            return Err(Interrupt::Failed(invalid_field(
                "This setting has nothing to scan",
                field,
            )));
        }
        let request = json!({
            "protocol": PROTOCOL,
            "op": "scan",
            "handler": self.spec.id,
            "field": field,
            "settings": settings.declared(),
        });
        self.call(Op::Scan, &request, cancel)
            .map(Collected::into_choices)
    }

    fn watched(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<Vec<PathBuf>, Interrupt<UserError>> {
        if !self.watches {
            return Ok(Vec::new());
        }
        let request = json!({
            "protocol": PROTOCOL,
            "op": "watch",
            "handler": self.spec.id,
            "settings": settings.declared(),
        });
        self.call(Op::Watch, &request, cancel)
            .map(Collected::into_watched)
    }

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        let request = json!({
            "protocol": PROTOCOL,
            "op": "resolve",
            "handler": self.spec.id,
            "settings": settings.declared(),
        });
        self.call(Op::Resolve, &request, cancel)
            .map(|collected| collected.into_playlists(&self.spec.name))
    }
}
