// Dev-only render benchmark, run by `make bench-browser`: opens frontend/bench.html (built into
// artifacts/bench-browser/site) in an Electron window and drives it. The page runs the real VideoRenderer; this file
// only starts it, runs the scenarios one at a time and prints and saves what the page reports.
//   BENCH_DISPLAY=real  use the machine's GPU (default: SwiftShader, Chromium's software renderer, which proves the
//                       bench works but whose numbers do not count)
//   BENCH_SMOKE=1, BENCH_SECONDS, BENCH_RESOLUTIONS, BENCH_CATALOGS, BENCH_LANES, BENCH_COUNTS, BENCH_MODES
//                       narrow the matrix (frontend/src/bench/plan.ts)
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const OUT = path.resolve(__dirname, "../artifacts/bench-browser");
const SITE = path.join(OUT, "site");
const real = process.env.BENCH_DISPLAY === "real";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };

// The profile goes to a throwaway folder: the bench never touches the user's own data.
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "ax-devil-bench-")));
const switches = [["force-device-scale-factor", "1"], ["disable-renderer-backgrounding"]];
if (!real)
  switches.push(
    ["use-gl", "angle"],
    ["use-angle", "swiftshader"],
    ["enable-unsafe-swiftshader"],
    ["ignore-gpu-blocklist"],
  );
for (const [name, value] of switches) app.commandLine.appendSwitch(name, value);

// A loopback server for the built page: module scripts do not load from file:// everywhere.
function serve() {
  const server = http.createServer((request, response) => {
    const name = request.url === "/" ? "bench.html" : decodeURIComponent(request.url.split("?")[0]);
    const file = path.join(SITE, name);
    if (!file.startsWith(SITE + path.sep) || !fs.existsSync(file)) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, {
      "content-type": TYPES[path.extname(file)] ?? "application/octet-stream",
      // Cross-origin isolation gives performance.now() microseconds instead of 0.1 ms steps.
      "cross-origin-opener-policy": "same-origin",
      "cross-origin-embedder-policy": "require-corp",
    });
    fs.createReadStream(file).pipe(response);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

const settings = {
  smoke: process.env.BENCH_SMOKE,
  seconds: process.env.BENCH_SECONDS,
  resolutions: process.env.BENCH_RESOLUTIONS,
  catalogs: process.env.BENCH_CATALOGS,
  lanes: process.env.BENCH_LANES,
  counts: process.env.BENCH_COUNTS,
  modes: process.env.BENCH_MODES,
};

async function run() {
  const server = await serve();
  const window = new BrowserWindow({
    width: 1280,
    height: 720,
    webPreferences: { sandbox: true, contextIsolation: true, backgroundThrottling: false },
  });
  await window.loadURL(`http://127.0.0.1:${server.address().port}/bench.html`);
  const js = (expression) => window.webContents.executeJavaScript(expression);
  const environment = await js("benchApi.environment()");
  const scenarios = await js(`benchApi.plan(${JSON.stringify(settings)})`);
  console.log(await js(`benchApi.header(${JSON.stringify(environment)})`));
  console.log(`\n${scenarios.length} scenarios, ${scenarios[0]?.seconds ?? 0} s each\n`);
  const results = [];
  for (const scenario of scenarios) {
    const { catalog, resolution, lanes, objects, mode } = scenario;
    process.stderr.write(
      `[${results.length + 1}/${scenarios.length}] ${catalog} ${resolution} ${lanes}x${objects} ${mode}\n`,
    );
    results.push(await js(`benchApi.run(${JSON.stringify(scenario)})`));
  }
  const notes = [`GPU features: ${JSON.stringify(app.getGPUFeatureStatus())}`];
  const { text, pass } = await js(
    `benchApi.report(${JSON.stringify(environment)}, ${JSON.stringify(results)}, ${JSON.stringify(notes)})`,
  );
  console.log(text);
  const machine = {
    display: real ? "real" : "swiftshader",
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    platform: `${os.platform()} ${os.release()}`,
    cpu: os.cpus()[0]?.model,
  };
  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, "result.json");
  fs.writeFileSync(
    file,
    JSON.stringify({ machine, environment, settings, pass, results }, null, 2),
  );
  console.log(`\nJSON: ${file}`);
  server.close();
  return 0;
}

app
  .whenReady()
  .then(run)
  .then(
    (code) => app.exit(code),
    (error) => {
      console.error(error);
      app.exit(1);
    },
  );
