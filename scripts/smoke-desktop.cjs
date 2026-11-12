// Runs the desktop smoke scenarios the way `make test-desktop SMOKE=<names>` asks: one fresh app launch per selected
// group, one after another. A name is a group (all of it) or a scenario (it and the ones before it in its group, which
// leave the state it needs). Empty means every group. `--plan <names>` prints one `<group> <scenarios> <deadline seconds>` line per
// launch: the deadline is how long the launcher waits for that launch's verdict (scripts/smoke-packaged.sh waits as long).
const { supervise } = require("./smoke-process.cjs");
const fs = require("node:fs");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");

const desktop = path.join(__dirname, "..", "desktop");
// What a launch needs beside its own start and scenarios: the app starting, a failure picture, the backend stopping.
const { LAUNCH_MARGIN_MS } = require("../desktop/smoke-budget.cjs");
let cancelled = false;

/** Prints what is wrong and stops, before anything is launched. */
function refuse(message, groups) {
  process.stderr.write(`${message}\n`);
  if (groups)
    for (const [group, names] of Object.entries(groups))
      process.stderr.write(`  ${group}: ${names.join(", ")}\n`);
  process.exit(2);
}

/**
 * Loads a scenario module in plain Node, where `require("electron")` would be a path string (and, with no Electron
 * installed, a download). It gets a stub that hands out the API objects but throws on any use of one, so a module
 * that calls Electron while loading fails here; nothing is installed, downloaded or printed.
 */
function load(file) {
  const unusable = () => {
    throw new Error("it uses an Electron API while loading; do that inside run()");
  };
  const api = new Proxy(function () {}, {
    get: unusable,
    set: unusable,
    apply: unusable,
    construct: unusable,
  });
  const stub = new Proxy({}, { get: () => api });
  const original = Module._load;
  Module._load = (request, ...rest) =>
    request === "electron" ? stub : original.call(Module, request, ...rest);
  try {
    return require(file);
  } catch (error) {
    return refuse(`${path.relative(process.cwd(), file)} failed to load: ${error.message}`);
  } finally {
    Module._load = original;
  }
}

/** The scenario modules by name, each checked to export `run`. */
function loadScenarios(groups) {
  const listed = Object.entries(groups).flatMap(([group, names]) =>
    names.map((name) => [group, name]),
  );
  // One grammar for group and scenario names, which are file names and plan fields: lowercase words joined by hyphens.
  const grammar = /^[a-z][a-z0-9-]*$/;
  for (const group of Object.keys(groups))
    if (!grammar.test(group))
      refuse(
        `Group "${group}" in desktop/smoke-groups.cjs must match ${grammar} (lowercase, digits, hyphens)`,
      );
  const seen = new Set();
  for (const [group, name] of listed) {
    if (!grammar.test(name))
      refuse(
        `Scenario "${name}" in group "${group}" must match ${grammar} (lowercase, digits, hyphens)`,
      );
    if (seen.has(name)) refuse(`Scenario "${name}" is listed twice in desktop/smoke-groups.cjs`);
    seen.add(name);
    if (name in groups)
      refuse(`"${name}" is both a group and a scenario in desktop/smoke-groups.cjs`);
    const file = path.join(desktop, `smoke-${name}.cjs`);
    if (!fs.existsSync(file))
      refuse(`Group "${group}" lists "${name}", but desktop/smoke-${name}.cjs does not exist`);
    if (typeof load(file).run !== "function")
      refuse(`Group "${group}" lists "${name}", but desktop/smoke-${name}.cjs does not export run`);
  }
  const scenarios = new Map();
  for (const file of fs.readdirSync(desktop).filter((f) => /^smoke-.+\.cjs$/.test(f))) {
    const name = file.slice("smoke-".length, -".cjs".length);
    const module = load(path.join(desktop, file));
    if (typeof module.run !== "function") continue;
    if (!seen.has(name))
      refuse(
        `desktop/${file} exports run but no group in desktop/smoke-groups.cjs lists "${name}"`,
      );
    scenarios.set(name, module);
  }
  return scenarios;
}

/** The launches a selection asks for, as `[group, names]` in groups-file order. */
function plan(groups, selection) {
  const tokens = selection
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean);
  if (tokens.length === 0 && selection.trim() !== "")
    refuse(
      `SMOKE selects nothing: "${selection}"; name a group or scenario, or leave it empty for everything`,
    );
  const need = new Map();
  for (const token of tokens) {
    if (Object.hasOwn(groups, token)) {
      need.set(token, groups[token].length);
      continue;
    }
    const group = Object.keys(groups).find((key) => groups[key].includes(token));
    if (!group)
      refuse(`No group or scenario named "${token}". Groups and their scenarios:`, groups);
    need.set(group, Math.max(need.get(group) ?? 0, groups[group].indexOf(token) + 1));
  }
  return Object.entries(groups)
    .filter(([group]) => tokens.length === 0 || need.has(group))
    .map(([group, names]) => [group, names.slice(0, need.get(group) ?? names.length)]);
}

/**
 * How long a launch of these scenarios may run without a verdict: the start's budget, every scenario's timeout and the
 * launch margin, slowed down as asked. Each inner timeout is shorter, so a hang is named by the app before this fires.
 */
function deadlineMs(names, scenarios) {
  const slowdown = Math.max(1, Number(process.env.AX_DEVIL_SMOKE_SLOWDOWN) || 1);
  const { SCENARIO_TIMEOUT_MS, START_TIMEOUT_MS } = require(path.join(desktop, "smoke-kit.cjs"));
  return (
    (LAUNCH_MARGIN_MS +
      START_TIMEOUT_MS +
      names.reduce(
        (total, name) => total + (scenarios.get(name).timeoutMs ?? SCENARIO_TIMEOUT_MS),
        0,
      )) *
    slowdown
  );
}

function launch(group, names, scenarios, result) {
  return supervise(
    path.join(__dirname, "isolated"),
    [
      group === "native" ? "--window-manager" : "--display",
      "pnpm",
      "--dir",
      "desktop",
      "exec",
      "electron",
      ".",
      "--ozone-platform=x11",
      "--smoke-test",
    ],
    result,
    { group, names, limitMs: deadlineMs(names, scenarios) },
  );
}

async function main() {
  const args = process.argv.slice(2);
  const planOnly = args[0] === "--plan";
  const groups = require(path.join(desktop, "smoke-groups.cjs"));
  const scenarios = loadScenarios(groups);
  const launches = plan(groups, (planOnly ? args[1] : args[0]) ?? "").filter(
    ([group]) => group !== "native" || process.platform === "linux",
  );
  if (launches.length === 0)
    refuse("Nothing to run: the selection names no group or scenario.", groups);
  if (planOnly) {
    for (const [group, names] of launches)
      process.stdout.write(
        `${group} ${names.join(",")} ${Math.ceil(deadlineMs(names, scenarios) / 1000)}\n`,
      );
    return;
  }
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-devil-smoke-"));
  for (const signal of ["SIGINT", "SIGTERM"])
    process.on(signal, () => {
      cancelled = true;
    });
  const verdicts = [];
  for (const [group, names] of launches) {
    if (cancelled) break;
    verdicts.push([group, await launch(group, names, scenarios, path.join(folder, group))]);
  }
  fs.rmSync(folder, { recursive: true, force: true });
  process.stdout.write("\n");
  for (const [group, { ok, seconds, reason }] of verdicts)
    process.stdout.write(
      `${group} ${ok ? "PASS" : "FAIL"} ${seconds}s${reason ? ` ${reason}` : ""}\n`,
    );
  if (cancelled) process.stdout.write("cancelled: no further group was started\n");
  process.exitCode = !cancelled && verdicts.every(([, { ok }]) => ok) ? 0 : 1;
}

if (require.main === module) void main();

module.exports = { loadScenarios, deadlineMs };
