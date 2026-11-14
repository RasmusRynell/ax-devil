//! The program of a plugin: what every one of its handlers runs, shared by them.

use std::sync::Mutex;

use foundation::CancelToken;
use handler_api::Values;
use serde_json::{Map, Value};

use crate::{
    code,
    manifest::{Manifest, ManifestError},
    run::{self, End, LineHandler, Process},
};

/// A plugin's command, environment, idle timeout, settings and code identity. One per plugin, held by each of its
/// handlers.
pub struct Program {
    manifest: Manifest,
    /// The plugin's settings, sent in every request the program gets and part of its code identity.
    settings: Values,
    /// The files the identity hashes, as last seen, with their hash.
    code: Mutex<Option<(Vec<code::CodeFile>, [u8; 32])>>,
}

impl Program {
    /// The program of a validated manifest, run with `settings` (from [`Manifest::settings_values`]).
    pub fn new(manifest: Manifest, settings: Values) -> Self {
        Self {
            manifest,
            settings,
            code: Mutex::new(None),
        }
    }

    /// The plugin's settings as a request carries them, exactly as given: no value is expanded or rewritten.
    pub(crate) fn settings(&self) -> Map<String, Value> {
        self.settings.declared()
    }

    /// What the plugin declares about itself.
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    /// The identity of the plugin's code: SHA-256 of the manifest as it was loaded (the one that runs), its settings
    /// and the files it names, which the scene store keeps next to an artifact so an edited plugin rebuilds. Files are read again
    /// only when their size or modification time changed since the last call.
    pub fn code_hash(&self) -> Result<[u8; 32], ManifestError> {
        let files = code::collect(&self.manifest, code::LIMITS)?;
        let mut memo = self
            .code
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some((seen, hash)) = &*memo
            && *seen == files
        {
            return Ok(*hash);
        }
        let hash = code::digest(&self.manifest, &self.settings.canonical(), &files)
            .map_err(|error| ManifestError::new("code", error.to_string()))?;
        *memo = Some((files, hash));
        Ok(hash)
    }

    /// One run of the plugin for the handler `handler`, which is sent `request` on stdin; its output lines go to
    /// `lines`.
    pub(crate) fn run(
        &self,
        handler: &str,
        request: &Value,
        lines: &mut dyn LineHandler,
        cancel: &CancelToken,
    ) -> End {
        match self.start(handler) {
            Ok(process) => run::run(process, request.to_string(), lines, cancel),
            Err(reason) => End::Incomplete(reason),
        }
    }

    /// Starts the plugin's process for the handler `handler`, which the caller feeds and reads.
    pub(crate) fn start(&self, handler: &str) -> Result<Process, String> {
        let Some((program, args)) = self.manifest.command.split_first() else {
            return Err("The plugin has no command".to_owned());
        };
        Process::start(&run::Spec {
            id: handler,
            program,
            args,
            env: &self.manifest.env,
            idle_timeout: self.manifest.idle_timeout,
        })
    }
}
