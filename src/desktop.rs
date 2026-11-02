//! Finding the desktop folder and the Electron in it, for the shell and for commands that run TypeScript.

use std::path::{Path, PathBuf};

/// The desktop folder: `AX_DEVIL_DESKTOP`, else the one in the checkout the binary was built in.
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

/// The Electron binary the desktop folder installed, or why there is none.
pub fn electron(desktop: &Path) -> Result<PathBuf, String> {
    let binary = if cfg!(windows) {
        "electron.cmd"
    } else {
        "electron"
    };
    let electron = desktop.join("node_modules/.bin").join(binary);
    if electron.is_file() {
        Ok(electron)
    } else {
        Err(format!(
            "Cannot find the desktop shell's Electron at {}; set AX_DEVIL_DESKTOP to the desktop folder, \
             installed with pnpm install",
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
