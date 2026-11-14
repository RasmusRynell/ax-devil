// Desktop smoke test and screenshot harness, run by `make test-desktop` on a private display. One launch is one group of
// scenarios (`smoke-groups.cjs`): the launcher names them in `AX_DEVIL_SMOKE_RUN`, and they run in that order after the
// start every launch begins with.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCENARIO_TIMEOUT_MS,
  START_TIMEOUT_MS,
  SCREENSHOTS,
  SMOKE_ROOT,
  begin,
  current,
  page,
  reload,
  saveFailure,
  setView,
  slowdown,
  until,
  watchCsp,
} = require("./smoke-kit.cjs");

// The first page of a first run, which every scenario starts from: it breaks no Content Security Policy rule, and Quick
// Setup fits the smallest window until Done closes it.
async function start({ window, ownerCsp }) {
  const { js, wait } = page(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  assert.equal(await js("document.querySelector('[role=alert]')"), null);

  // No page of the app may break its Content Security Policy. A reload covers the load itself; a request the
  // policy forbids shows the collector sees violations at all.
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  await js("fetch('https://example.invalid/').catch(() => {})");
  await until(() => ownerCsp.length > 0, "the policy to refuse a request to another origin");
  ownerCsp.length = 0;

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  // The isolated home is a first run, so Quick Setup is open: check it in the smallest window, then Done closes it.
  const size = window.getSize();
  await wait("document.querySelector('dialog[open] h2')?.textContent === 'Quick Setup'");
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    await setView(window, [640, 480], zoom);
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await frame();
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `Quick Setup must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await shoot(`quick-setup-${theme}${zoom === 1 ? "" : "-200"}.png`);
    if (zoom === 1) {
      // The largest text size previews live and the dialog still fits; System goes back before Done stores it.
      const textSize = async (value) => {
        await js(`document.querySelector("input[type=radio][value=${value}]").click()`);
        await wait(`document.documentElement.dataset.textSize === "${value}"`);
        await frame();
      };
      await textSize("larger");
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        "Quick Setup must fit the 640x480 window at the largest text size",
      );
      await shoot(`quick-setup-larger-${theme}.png`);
      await textSize("system");
    }
  }
  window.webContents.setZoomFactor(1);
  await js(
    "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done').click()",
  );
  await wait("!document.querySelector('dialog')");
  window.setSize(...size);
}

// Runs `work` for at most `limit` ms (longer under AX_DEVIL_SMOKE_SLOWDOWN); a scenario that hangs fails the launch
// naming itself and the step it was in, instead of leaving the launch to its outer deadline. Expiry settles the outcome
// at once; the window's picture is taken afterwards, so a scenario that finishes meanwhile cannot undo the failure.
async function within(window, name, limit, work) {
  let timer;
  let expiry;
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const seconds = (limit * slowdown()) / 1000;
      expiry = new Error(
        `Smoke scenario "${name}" timed out after ${seconds} s, in "${current()}"`,
      );
      reject(expiry);
    }, limit * slowdown());
  });
  const running = work();
  // The abandoned scenario may still fail later, after the launch has reported.
  running.catch(() => undefined);
  try {
    await Promise.race([running, expired]);
  } catch (error) {
    if (error === expiry) error.message = `${error.message}\n${await saveFailure(window)}`;
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = async (window, shell) => {
  const names = (process.env.AX_DEVIL_SMOKE_RUN ?? "").split(",").filter(Boolean);
  assert.ok(
    names.length > 0,
    "Smoke runs go through `make test-desktop [SMOKE=<groups or scenarios>]`",
  );
  const ownerCsp = watchCsp(window);
  const ctx = { window, shell, ownerCsp };
  await within(window, "start", START_TIMEOUT_MS, () => start(ctx));
  for (const name of names) {
    begin(name);
    console.log(`smoke: running ${name}`);
    const scenario = require(`./smoke-${name}.cjs`);
    await within(window, name, scenario.timeoutMs ?? SCENARIO_TIMEOUT_MS, () => scenario.run(ctx));
  }
  begin("end");
  assert.deepEqual(ownerCsp, [], "The app must break no CSP rule");
  console.log(
    `PASS: ${names.join(", ")}; screenshots in ${path.relative(SMOKE_ROOT, SCREENSHOTS)}`,
  );
};
