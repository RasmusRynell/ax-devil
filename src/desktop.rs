//! Finding the desktop folder and the Electron in it, for the shell and for commands that run TypeScript.

use std::path::{Path, PathBuf};

/// The desktop folder: `AX_DEVIL_DESKTOP` (the shell sets it, to the app's own folder when packaged), else the one in
/// the checkout the binary was built in.
pub fn folder() -> Result<PathBuf, std::io::Error> {
    Ok(match std::env::var_os("AX_DEVIL_DESKTOP") {
        Some(folder) => PathBuf::from(folder),
        // target/<profile>/ax-devil sits three levels below the checkout that holds desktop/.
        None => std::env::current_exe()?
            .ancestors()
            .nth(3)
            .map(|root| root.join("desktop"))
            .unwrap_or_default(),
    })
}

/// The Electron binary: `AX_DEVIL_ELECTRON` (the executable of the shell that started this backend, which in a
/// packaged app is the app itself), else the one the desktop folder installed. Or why there is none.
pub fn electron(desktop: &Path) -> Result<PathBuf, String> {
    let binary = if cfg!(windows) {
        "electron.cmd"
    } else {
        "electron"
    };
    let electron = std::env::var_os("AX_DEVIL_ELECTRON").map_or_else(
        || desktop.join("node_modules/.bin").join(binary),
        PathBuf::from,
    );
    if electron.is_file() {
        Ok(electron)
    } else {
        Err(format!(
            "Cannot find the desktop shell's Electron at {}; set AX_DEVIL_DESKTOP to the desktop folder, \
             installed with pnpm install, or AX_DEVIL_ELECTRON to the executable",
            electron.display()
        ))
    }
}

/// The page the frontend build writes for the hidden renderer, which `desktop/render.cjs` loads, or why there is
/// none.
pub fn render_page(desktop: &Path) -> Result<PathBuf, String> {
    let page = desktop.join("generated/render/render.html");
    if page.is_file() {
        Ok(page)
    } else {
        Err(format!(
            "Cannot find the render page at {}; build it with pnpm --dir frontend build",
            page.display()
        ))
    }
}

/// The catalog compiler the frontend build writes for `catalog check`, which runs in Electron-as-Node, or why there
/// is none.
pub fn catalog_checker(desktop: &Path) -> Result<PathBuf, String> {
    let checker = desktop.join("generated/catalog-check.cjs");
    if checker.is_file() {
        Ok(checker)
    } else {
        Err(format!(
            "Cannot find the catalog checker at {}; build it with pnpm --dir frontend build",
            checker.display()
        ))
    }
}
