const { pressShortcut } = require("./shortcut-input.cjs");
const { clickVisible } = require("./settings-smoke-kit.cjs");
// The kit gallery scenario of the desktop smoke test: the page at `#kit-gallery` shows every shared component, is
// photographed in both themes, and is checked where components must wrap instead of overflowing.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, delay, frames, page, reload, setView } = require("./smoke-kit.cjs");

// Runs in the page: what leaves the 20-character boxes the gallery narrows components into, or overlaps in them.
function narrowProblems() {
  const problems = [];
  const boxes = [...document.querySelectorAll("main div.border-dashed")];
  if (boxes.length < 3) problems.push(`expected 3 narrow boxes, found ${boxes.length}`);
  for (const box of boxes) {
    const edge = box.getBoundingClientRect();
    const name = box.textContent.trim().slice(0, 20);
    for (const element of box.querySelectorAll("*")) {
      const rect = element.getBoundingClientRect();
      if (rect.width && (rect.right > edge.right + 1 || rect.left < edge.left - 1))
        problems.push(`${element.localName} leaves the narrow box "${name}"`);
    }
  }
  // A label and its control stack when the box is too narrow for both on a line.
  for (const label of document.querySelectorAll("main div.border-dashed label")) {
    const control =
      label.querySelector("select, input") ?? label.parentElement.querySelector("input");
    if (!control || control.type === "checkbox") continue;
    // The wrapping label of a Select holds its text and the control; the TextField's label is only the text.
    const text = label.contains(control)
      ? [...label.childNodes].find((node) => node.nodeType === 3)
      : label;
    const range = document.createRange();
    range.selectNodeContents(text);
    const words = range.getBoundingClientRect();
    const box = control.getBoundingClientRect();
    const across = Math.min(words.right, box.right) - Math.max(words.left, box.left);
    const down = Math.min(words.bottom, box.bottom) - Math.max(words.top, box.top);
    if (across > 1 && down > 1) problems.push(`label and control overlap: ${text.textContent}`);
  }
  // The narrow banner's actions sit under its message instead of squeezing it.
  const banner = document.querySelector("main div.border-dashed [role=status]");
  const message = banner?.querySelector("p").getBoundingClientRect();
  const actions = banner?.querySelector("button")?.getBoundingClientRect();
  if (message && actions && actions.top < message.bottom - 1)
    problems.push("the narrow banner's actions overlap its message");
  return problems;
}

// Runs in the page: wide-layout checks for the whole gallery.
function galleryProblems() {
  const problems = [];
  const main = document.querySelector("main");
  if (main.scrollWidth > main.clientWidth) problems.push("the gallery scrolls sideways");
  const icons = [...document.querySelectorAll("section[aria-label=Icons] li")];
  if (icons.length < 50) problems.push(`only ${icons.length} icons shown`);
  for (const item of icons) {
    const mark = item.querySelector("span").getBoundingClientRect();
    if (!mark.width || !mark.height) problems.push(`icon without size: ${item.textContent}`);
    if (
      item.querySelector("span.truncate").scrollWidth >
      item.querySelector("span.truncate").clientWidth
    )
      problems.push(`icon name cut off: ${item.textContent}`);
  }
  // Toggle chips wrap whole onto several lines in the narrow row, and none is cut.
  const row = document.querySelector("[aria-label='Filters, narrow']");
  const tops = new Set(
    [...row.children].map((chip) => Math.round(chip.getBoundingClientRect().top)),
  );
  if (tops.size < 2) problems.push("the narrow chip row does not wrap");
  for (const chip of row.children)
    if (chip.getBoundingClientRect().right > row.getBoundingClientRect().right + 1)
      problems.push(`chip leaves its row: ${chip.textContent}`);
  // The long chip ends in an ellipsis inside its box, and the long banner message wraps in full.
  const long = document.querySelector("[aria-label='Long chip'] span.truncate");
  if (long.scrollWidth <= long.clientWidth) problems.push("the long chip is not truncated");
  // Every heading of a caption is whole.
  for (const heading of document.querySelectorAll("section[aria-label=Captions] h2"))
    if (heading.scrollWidth > heading.clientWidth + 1)
      problems.push(`caption heading cut off: ${heading.textContent}`);
  return problems;
}

// Runs a check in the page, with an error in it reported as a problem instead of an opaque failure.
const inPage = (check) =>
  `(() => { try { return (${check.toString()})(); } catch (error) { return [String(error.stack)]; } })()`;

const key = (window, chord) => pressShortcut(window, chord, { character: true });

async function gallery(window) {
  const { js, wait } = page(window);
  const size = window.getSize();
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  await js("history.replaceState(null, '', '#kit-gallery')");
  await reload(window);
  await wait("document.querySelector('section[aria-label=Banner]')");
  await setView(window, [1280, 1000], 1);
  const answer = () =>
    js("document.querySelector('output[aria-label=\"Last choice\"]').textContent");
  const dialogOpen = () => js("Boolean(document.querySelector('dialog[open]'))");
  const pickerOpener =
    "[...document.querySelectorAll('button')].find((b) => b.textContent === 'Open path picker')";
  const clickButton = async (text) => {
    const target = `[...document.querySelectorAll('button')].find(b => b.textContent === ${JSON.stringify(text)})`;
    await js(`${target}?.scrollIntoView({block: 'center'})`);
    await clickVisible(window, target);
  };
  const openPicker = async () => {
    await setView(window, [1280, 1000], 1);
    await clickButton("Open path picker");
    await wait("document.querySelector('dialog[open] [role=group] button')");
    await wait("document.activeElement?.labels?.[0]?.textContent === 'Name'");
    await frames({ js });
  };

  const closePicker = async () => {
    key(window, "Escape");
    await wait("!document.querySelector('dialog[open]')");
    assert.equal(
      await js(`document.activeElement === ${pickerOpener}`),
      true,
      "the path picker restores its opener's focus",
    );
  };

  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await frames({ js });
    assert.deepEqual(await js(inPage(galleryProblems)), [], `kit gallery, ${theme}`);
    assert.deepEqual(await js(inPage(narrowProblems)), [], `narrow boxes, ${theme}`);
    await shoot(`kit-gallery-${theme}.png`);

    // A choice dialog ignores Escape and answers with its default on Enter.
    await clickButton("Open choice dialog");
    await wait("document.querySelector('dialog[open]')");
    await wait("document.activeElement?.textContent === 'Later'");
    await shoot(`kit-choice-dialog-${theme}.png`);
    key(window, "Escape");
    await delay(300);
    assert.ok(await dialogOpen(), `Escape must not close the choice dialog (${theme})`);
    assert.equal(
      await js("document.activeElement?.textContent"),
      "Later",
      "Escape keeps the default focused",
    );
    key(window, "Escape");
    await delay(300);
    assert.ok(await dialogOpen(), `A second Escape must not close the choice dialog (${theme})`);
    key(window, "Return");
    await wait("!document.querySelector('dialog[open]')");
    assert.equal(await answer(), "later", `Enter must give the default choice (${theme})`);

    // The path picker lists the server's folders; Escape cancels it.
    await openPicker();
    await shoot(`kit-path-picker-${theme}.png`);
    await closePicker();
  }

  // The dialog's long message scrolls and its choices stay in view at the window minimum and 200% zoom.
  await setView(window, [640, 480], 2);
  await clickButton("Open choice dialog");
  await wait("document.querySelector('dialog[open]')");
  await wait("document.activeElement?.textContent === 'Later'");
  await frames({ js });
  assert.deepEqual(await js(`(${fit.dialog.toString()})()`), [], "the choice dialog at 200% zoom");
  key(window, "Return");
  await wait("!document.querySelector('dialog[open]')");
  // The path picker's list scrolls and its buttons stay in view at the same size.
  await openPicker();
  await setView(window, [640, 480], 2);
  await frames({ js });
  assert.deepEqual(await js(`(${fit.dialog.toString()})()`), [], "the path picker at 200% zoom");
  await closePicker();
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await js("document.documentElement.dataset.textSize = 'larger'");
    await openPicker();
    await setView(window, [640, 480], 2);
    await frames({ js });
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      "path picker, largest text at 200%",
    );
    await shoot(`appearance-path-picker-${theme}-2.png`);
    await clickVisible(
      window,
      "[...document.querySelectorAll('dialog[open] button')].find(b => b.textContent === 'Cancel')",
    );
    await wait("!document.querySelector('dialog[open]')");
  }
  window.webContents.setZoomFactor(1);

  await js("history.replaceState(null, '', location.pathname + location.search)");
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  window.setSize(...size);
  await frames({ js });
}

module.exports = { run: ({ window }) => gallery(window) };
