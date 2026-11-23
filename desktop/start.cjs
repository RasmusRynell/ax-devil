// What the Electron executable runs: the hidden renderer when the backend starts it with --render-host (export, catalog
// render), else the shell. One executable serves both because a packaged Electron runs its own app whatever script it
// is given.
require(process.argv.includes("--render-host") ? "./render.cjs" : "./main.cjs");
