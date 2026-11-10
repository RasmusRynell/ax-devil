// The smoke test's reload scenario: the overlay file of an open video is written on disk, the viewer offers to reload
// (checked in both themes, the smallest window and 200% zoom) and keeps what it shows until Reload is pressed; a reload
// that fails (the file is gone) keeps it too and says why, checked the same way. Only the
// command line can give a video an overlay, so this runs on a backend of its own, like the repeated-overlay scenario.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { VIDEO, inBackendWindow } = require("./smoke-twice.cjs");
const {
  frames,
  goToFrame,
  laneCells,
  page,
  removeTemp,
  SCREENSHOTS,
  setView,
  SHOWN,
  SMOKE_ROOT,
} = require("./smoke-kit.cjs");

const OVERLAY = path.join(SMOKE_ROOT, "fixtures/decoders/onvif_xml/frames.xml");
const OFFER =
  "[...document.querySelectorAll('[role=status]')].find((e) => /Source changed/.test(e.textContent))";

const FAILED =
  "[...document.querySelectorAll('[role=alert]')].find((e) => /Could not reload/.test(e.textContent))";

/**
 * Runs in the page: how the offer sits in the window, as readable problems. It lies inside the window, its text is not
 * cut off, and each of its buttons is whole inside it and not covered.
 */
function offerFits() {
  const offer = OFFER_IN_PAGE;
  if (!offer) return ["the offer is not there"];
  const problems = [];
  const box = offer.getBoundingClientRect();
  if (box.left < 0 || box.top < 0 || box.right > innerWidth + 1 || box.bottom > innerHeight + 1)
    problems.push(`the offer is outside the window: ${JSON.stringify(box)}`);
  for (const element of offer.querySelectorAll("*"))
    if (element.clientWidth > 2 && element.scrollWidth > element.clientWidth + 1)
      problems.push(`cut off: "${element.textContent.trim().slice(0, 30)}"`);
  for (const button of offer.querySelectorAll("button")) {
    const rect = button.getBoundingClientRect();
    if (rect.left < box.left - 1 || rect.right > box.right + 1 || rect.bottom > box.bottom + 1)
      problems.push(`outside the offer: ${button.ariaLabel || button.textContent}`);
    const top = document.elementFromPoint(
      (rect.left + rect.right) / 2,
      (rect.top + rect.bottom) / 2,
    );
    if (!button.contains(top)) problems.push(`covered: ${button.ariaLabel || button.textContent}`);
  }
  return problems;
}

/** Checks the banner `banner` (an expression for it in the page) fits, and saves it as `name`, in both themes at the normal view and the smallest at 200%. */
async function shoot(window, banner, name) {
  const { js, wait } = page(window);
  for (const { theme, size, zoom } of [
    { theme: "light", size: [1100, 720], zoom: 1 },
    { theme: "dark", size: [1100, 720], zoom: 1 },
    { theme: "light", size: [640, 480], zoom: 2 },
    { theme: "dark", size: [640, 480], zoom: 2 },
  ]) {
    const suffix = zoom === 1 ? "" : "-200";
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await setView(window, size, zoom);
    // The sidebar fills the smallest window, so it is closed there.
    if (zoom === 2 && (await js("!!document.querySelector('[aria-label=\"Workspace items\"]')")))
      await js("document.querySelector('[aria-label=Workspace]').click()");
    // Hidden behind the sidebar it would have no size, which fits anywhere.
    await wait(`${banner}?.getBoundingClientRect().width > 0`);
    await frames({ js });
    assert.deepEqual(
      await js(`(${offerFits.toString().replace("OFFER_IN_PAGE", banner)})()`),
      [],
      `${name} must fit the ${size.join("x")} window at ${zoom * 100}% zoom`,
    );
    await fs.writeFile(
      path.join(SCREENSHOTS, `${name}-${theme}${suffix}.png`),
      (await window.webContents.capturePage()).toPNG(),
    );
  }
}

module.exports.run = async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-reload-"));
  const overlay = path.join(folder, "frames.xml");
  await fs.copyFile(OVERLAY, overlay);
  try {
    await inBackendWindow(
      ["--video", VIDEO, "--overlay", overlay, "--handler-type", "ONVIF_XML"],
      async (window) => {
        const { js, wait } = page(window);
        await wait(`${SHOWN} >= 0`);
        await goToFrame(window, 3);
        assert.equal(await js(`${OFFER} ?? null`), null, "no offer before anything changed");

        // The file is written (a newline more); the offer comes once it stopped changing, and the picture stays.
        await fs.appendFile(overlay, "\n");
        await wait(`!!${OFFER}`);
        assert.equal(await js(SHOWN), 3, "the viewer keeps the frame it shows");
        assert.equal((await laneCells(window)).length, 1, "the viewer keeps its lane");

        await shoot(window, OFFER, "reload-offer");
        await setView(window, [1100, 720], 1);

        // Dismiss takes the offer down; a later change brings it back; Reload reads the file again and takes it down.
        await js(`${OFFER}.querySelector('[aria-label=Dismiss]').click()`);
        await wait(`!${OFFER}`);
        await fs.appendFile(overlay, "\n");
        await wait(`!!${OFFER}`);
        await js(
          `[...${OFFER}.querySelectorAll('button')].find((b) => b.textContent === 'Reload').click()`,
        );
        await wait(`!${OFFER}`);
        await wait(`${SHOWN} >= 0`);
        await goToFrame(window, 0);
        assert.equal((await laneCells(window)).length, 1, "the reloaded viewer has its lane");

        // The file is gone, so the next reload fails: the viewer keeps its picture and says why until dismissed.
        await setView(window, [1100, 720], 1);
        await fs.rm(overlay);
        await wait(`!!${OFFER}`);
        await js(
          `[...${OFFER}.querySelectorAll('button')].find((b) => b.textContent === 'Reload').click()`,
        );
        await wait(`!!${FAILED}`);
        assert.equal((await laneCells(window)).length, 1, "the viewer keeps its lane");
        await shoot(window, FAILED, "reload-failed");
        await setView(window, [1100, 720], 1);
        await js(`${FAILED}.querySelector('[aria-label=Dismiss]').click()`);
        await wait(`!${FAILED}`);
        assert.equal((await laneCells(window)).length, 1, "dismissing leaves the viewer");
      },
    );
  } finally {
    await removeTemp(folder);
  }
};
