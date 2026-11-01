//! Where ax-devil keeps its files: the XDG folders, falling back to the home folder.
//!
//! The environment is passed in as a lookup so tests never change the process environment.

use std::{
    ffi::OsString,
    path::{Path, PathBuf},
};

/// Reads one environment variable.
pub type Env<'a> = &'a dyn Fn(&str) -> Option<OsString>;

/// The files and folders a launch uses, each from its command-line override or the XDG default.
pub struct Paths {
    /// The settings file. Its folder is created, private to the user, when the file is first written.
    pub config_file: PathBuf,
    /// The standard folder for cached video indexes and decoded frames.
    default_cache_dir: PathBuf,
    /// The `--cache-dir` override, which beats the `storage.cache_dir` setting.
    cache_override: Option<PathBuf>,
    /// The standard folder of the user's render catalogs.
    default_catalog_dir: PathBuf,
    /// The log file.
    pub log_file: PathBuf,
    /// The window positions, which are state rather than settings.
    pub window_file: PathBuf,
}

impl Paths {
    /// The paths for this launch; `config` and `cache_dir` are the `--config` and `--cache-dir` overrides.
    pub fn resolve(
        env: Env,
        config: Option<PathBuf>,
        cache_dir: Option<PathBuf>,
    ) -> Result<Self, String> {
        let state = folder(env, "XDG_STATE_HOME", ".local/state")?;
        Ok(Self {
            config_file: match config {
                Some(path) => path,
                None => folder(env, "XDG_CONFIG_HOME", ".config")?.join("settings.json"),
            },
            default_cache_dir: folder(env, "XDG_CACHE_HOME", ".cache")?,
            cache_override: cache_dir,
            default_catalog_dir: folder(env, "XDG_DATA_HOME", ".local/share")?.join("catalogs"),
            log_file: state.join("logs/ax-devil.log"),
            window_file: state.join("windows.json"),
        })
    }

    /// The folder for cached video indexes and decoded frames: `--cache-dir`, else the `storage.cache_dir`
    /// setting (`setting`), else the standard folder. The folder is not checked here; failing to use it is not fatal.
    pub fn cache_dir(&self, setting: Option<PathBuf>) -> PathBuf {
        let chosen = self.cache_override.clone().or(setting);
        chosen.unwrap_or_else(|| self.default_cache_dir.clone())
    }

    /// The folder of the user's render catalogs: the `storage.catalog_dir` setting (`setting`), else the standard
    /// folder. The folder need not exist, and nothing creates it.
    pub fn catalog_dir(&self, setting: Option<PathBuf>) -> PathBuf {
        setting.unwrap_or_else(|| self.default_catalog_dir.clone())
    }
}

/// The `ax-devil` folder under one XDG base: its variable when absolute, else `home_relative` under the home folder.
fn folder(env: Env, variable: &str, home_relative: &str) -> Result<PathBuf, String> {
    let absolute = |name: &str| {
        env(name)
            .map(PathBuf::from)
            .filter(|path| path.is_absolute())
    };
    let base = match absolute(variable) {
        Some(base) => base,
        None => absolute("HOME")
            .ok_or_else(|| format!("Cannot find the home folder: set HOME or {variable}"))?
            .join(Path::new(home_relative)),
    };
    Ok(base.join("ax-devil"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn paths(vars: &[(&str, &str)], config: Option<&str>) -> Result<Paths, String> {
        resolve(vars, config, None)
    }

    fn resolve(
        vars: &[(&str, &str)],
        config: Option<&str>,
        cache_dir: Option<&str>,
    ) -> Result<Paths, String> {
        let vars: HashMap<_, _> = vars.iter().copied().collect();
        let env = |name: &str| vars.get(name).map(OsString::from);
        Paths::resolve(
            &env,
            config.map(PathBuf::from),
            cache_dir.map(PathBuf::from),
        )
    }

    #[test]
    fn xdg_homes_win_when_absolute_and_the_home_folder_fills_in_the_rest() {
        let xdg = [
            ("HOME", "/home/me"),
            ("XDG_CONFIG_HOME", "/x/config"),
            ("XDG_STATE_HOME", "/x/state"),
            ("XDG_CACHE_HOME", "/x/cache"),
            ("XDG_DATA_HOME", "/x/data"),
        ];
        let found = paths(&xdg, None).unwrap();
        assert_eq!(
            found.config_file,
            Path::new("/x/config/ax-devil/settings.json")
        );
        assert_eq!(found.cache_dir(None), Path::new("/x/cache/ax-devil"));
        assert_eq!(
            found.catalog_dir(None),
            Path::new("/x/data/ax-devil/catalogs")
        );
        assert_eq!(
            found.log_file,
            Path::new("/x/state/ax-devil/logs/ax-devil.log")
        );
        assert_eq!(
            found.window_file,
            Path::new("/x/state/ax-devil/windows.json")
        );

        let relative = [
            ("HOME", "/home/me"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", ""),
        ];
        let found = paths(&relative, Some("/etc/mine.json")).unwrap();
        assert_eq!(found.config_file, Path::new("/etc/mine.json"));
        assert_eq!(found.cache_dir(None), Path::new("/home/me/.cache/ax-devil"));
        assert_eq!(
            found.catalog_dir(None),
            Path::new("/home/me/.local/share/ax-devil/catalogs")
        );
        assert_eq!(
            found.catalog_dir(Some("/mnt/mine".into())),
            Path::new("/mnt/mine")
        );
        assert_eq!(
            found.log_file,
            Path::new("/home/me/.local/state/ax-devil/logs/ax-devil.log")
        );
        assert_eq!(
            paths(&relative, None).unwrap().config_file,
            Path::new("/home/me/.config/ax-devil/settings.json")
        );
        assert!(paths(&[], None).is_err());
    }

    #[test]
    fn the_cache_folder_comes_from_the_command_line_then_the_setting_then_the_xdg_default() {
        let vars = [("HOME", "/home/me")];
        let setting = || Some(PathBuf::from("/mnt/big/cache"));
        let plain = resolve(&vars, None, None).unwrap();
        assert_eq!(plain.cache_dir(None), Path::new("/home/me/.cache/ax-devil"));
        assert_eq!(plain.cache_dir(setting()), Path::new("/mnt/big/cache"));
        let flagged = resolve(&vars, None, Some("/tmp/flag")).unwrap();
        assert_eq!(flagged.cache_dir(setting()), Path::new("/tmp/flag"));
    }
}
