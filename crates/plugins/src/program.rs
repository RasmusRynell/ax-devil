//! The program of a plugin: what every one of its handlers runs, shared by them.

use std::sync::Mutex;

use foundation::CancelToken;
use serde_json::Value;

use crate::{
    code,
    manifest::{Manifest, ManifestError},
    run::{self, End, LineHandler},
};

/// A plugin's command, environment, idle timeout and code identity. One per plugin, held by each of its handlers.
pub struct Program {
    manifest: Manifest,
    /// The files the identity hashes, as last seen, with their hash.
    code: Mutex<Option<(Vec<code::CodeFile>, [u8; 32])>>,
}

impl Program {
    /// The program of a validated manifest.
    pub fn new(manifest: Manifest) -> Self {
        Self {
            manifest,
            code: Mutex::new(None),
        }
    }

    /// What the plugin declares about itself.
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    /// The identity of the plugin's code: SHA-256 of the manifest as it was loaded (the one that runs) and the files
    /// it names, which the scene store keeps next to an artifact so an edited plugin rebuilds. Files are read again
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
        let hash = code::digest(&self.manifest, &files)
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
        let Some((command, args)) = self.manifest.command.split_first() else {
            return End::Incomplete("The plugin has no command".to_owned());
        };
        let process = run::Spec {
            id: handler,
            program: command,
            args,
            env: &self.manifest.env,
            request: request.to_string(),
            idle_timeout: self.manifest.idle_timeout,
        };
        run::run(&process, lines, cancel)
    }
}
