// The smoke test's check of a failed action of a pane's header menu, on a window whose eight panes are all taken, so
// Open to the Side fails. The message is said inside the pane that was right-clicked, whole in its tooltip, readable
// where the pane is the narrowest (160 CSS pixels), and it leaves Dismiss and the pane's own controls reachable.
const assert = require("node:assert/strict");
const fit = require("./fit.cjs");
const { moveTo, setView, until } = require("./smoke-kit.cjs");

/** The narrowest a pane gets in CSS pixels (`LEAST_PANE` in viewer/layout.ts), with a pixel of rounding. */
const LEAST_PANE_WIDTH = 160;
/** A realistic environment path, as a temp folder is: 60+ characters, where the real message is short. */
const LONG =
  "/home/runner/work/ax-devil/ax-devil/target/tmp/ax-devil-smoke-0123456789abcdef/clips/a-long-folder-name/take-3.mp4: the file was not found";

/** Runs in the page: where the pane's header was scrolled to the top, Dismiss is reached and the message stays in its pane. */
function messageProblems(panes, index) {
  const pane = panes()[index];
  const banner = pane.querySelector("[role=alert]");
  pane.querySelector("[draggable]").scrollIntoView({ block: "start", inline: "start" });
  const own = pane.getBoundingClientRect();
  const header = pane.querySelector("[draggable]").getBoundingClientRect();
  const problems = [];
  const control = banner.querySelector("button[aria-label=Dismiss]");
  const at = control.getBoundingClientRect();
  const [x, y] = [at.left + at.width / 2, at.top + at.height / 2];
  const hit = document.elementFromPoint(x, y);
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight || !hit || !control.contains(hit)) {
    const what = hit ? `${hit.tagName} ${hit.getAttribute("aria-label") ?? ""}` : "nothing";
    problems.push(
      `Dismiss is not reachable at ${Math.round(x)},${Math.round(y)} of ${innerWidth}x${innerHeight}, hit ${what}`,
    );
  }
  const box = banner.getBoundingClientRect();
  if (box.left < own.left - 1 || box.right > own.right + 1)
    problems.push("the message leaves its pane");
  if (box.top < header.bottom - 1) problems.push("the message covers the header");
  if (banner.scrollHeight > banner.clientHeight + 1)
    problems.push("the message wraps out of its row");
  return problems;
}

/** Runs in the page: scrolls the pane's transport into view and answers where the pointer rests to show it. */
function transportAt(panes, index) {
  const pane = panes()[index];
  pane
    .querySelector("[data-keeps-chrome=transport]")
    .scrollIntoView({ block: "nearest", inline: "nearest" });
  const r = pane.querySelector("[data-chrome-idle]").getBoundingClientRect();
  const [left, right] = [Math.max(r.left, 0), Math.min(r.right, innerWidth)];
  const [top, bottom] = [Math.max(r.top, 0), Math.min(r.bottom, innerHeight)];
  return { x: (left + right) / 2, y: (top + bottom) / 2 };
}

/** Runs in the page, with the transport shown: the play button is reached and the message does not overlap the transport. */
function controlProblems(panes, index) {
  const pane = panes()[index];
  const banner = pane.querySelector("[role=alert]");
  const play = pane.querySelector("button[aria-label=Pause], button[aria-label=Play]");
  if (!play) return ["the play button is missing"];
  const problems = [];
  const at = play.getBoundingClientRect();
  const [x, y] = [at.left + at.width / 2, at.top + at.height / 2];
  const hit = document.elementFromPoint(x, y);
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight || !hit || !play.contains(hit))
    problems.push(`the play button is not reachable at ${Math.round(x)},${Math.round(y)}`);
  const [m, c] = [
    banner.getBoundingClientRect(),
    pane.querySelector("[data-keeps-chrome=transport]").getBoundingClientRect(),
  ];
  if (c.top < m.bottom - 1 && c.bottom > m.top + 1)
    problems.push("the message overlaps the transport");
  return problems;
}

/** Runs in the page: says `text` as the message, as a failure with that text would. */
function say(panes, index, text) {
  const banner = panes()[index].querySelector("[role=alert]");
  banner.querySelector("p span").textContent = text;
  banner.parentElement.title = text;
}

/**
 * `panes` is the page expression for the list of panes. `both` runs a check in each theme and saves a screenshot, and
 * `sidebar` opens or closes the sidebar, as the split scenario has them.
 */
module.exports.headerFailure = async (window, { js, wait, both, sidebar, panes }) => {
  const list = `() => ${panes}`;
  const call = (fn, ...args) =>
    js(`(${fn.toString()})(${[list, ...args.map((a) => JSON.stringify(a))].join(", ")})`);
  const alert = (index) => `${panes}[${index}].querySelector('[role=alert]')`;

  const fail = async (index) => {
    await js(
      `(() => { const h = ${panes}[${index}].querySelector('[draggable]'); const r = h.getBoundingClientRect(); h.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: Math.round(r.left + 20), clientY: Math.round(r.top + r.height / 2) })); })()`,
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((i) => i.textContent === 'Open to the Side').click()",
    );
    await wait(alert(index));
    assert.equal(
      await js(`${alert(index)}.parentElement.title`),
      await js(`${alert(index)}.querySelector('p span').textContent`),
      "The tooltip says the whole message",
    );
  };
  // What the user must be able to do and read, in each theme: the message shows its first characters and the ellipsis
  // where it is cut (fit.cjs's own measurement of elided labels, on the message alone), Dismiss and the pane's controls
  // are reached by real geometry, and the area fits.
  const check = (index, name) =>
    both(name, async () => {
      assert.deepEqual(await js(`(${fit.split.toString()})()`), [], `${name} must fit`);
      assert.deepEqual(await call(messageProblems, index), [], `${name}: the message`);
      assert.deepEqual(
        await js(`(${fit.page.toString()})(${alert(index)})`),
        [],
        `${name}: the message is readable`,
      );
      const at = await call(transportAt, index);
      await moveTo(window, at.x, at.y);
      await until(
        () =>
          js(
            `Number(getComputedStyle(${panes}[${index}].querySelector('[data-keeps-chrome=transport]')).opacity) === 1`,
          ),
        "the transport is shown",
      );
      assert.deepEqual(await call(controlProblems, index), [], `${name}: the pane's controls`);
    });
  const dismiss = async (index) => {
    await js(`${alert(index)}.querySelector('button[aria-label=Dismiss]').click()`);
    await wait(`!${alert(index)}`);
  };

  // The first pane, which is wide, with the real message and a long one, then at the smallest window and 200% zoom.
  await fail(0);
  await check(0, "split-header-failed");
  await call(say, 0, LONG);
  await check(0, "split-header-failed-long");
  await sidebar(false);
  await setView(window, [640, 480], 2);
  await js("document.querySelector('main > div').scrollTo(0, 0)");
  await check(0, "split-header-failed-small");
  await setView(window, [1100, 720], 1);
  await sidebar(true);
  await dismiss(0);

  // A pane measured at the narrowest a pane gets: the message shows a readable start and its ellipsis, whole in the tooltip.
  const narrow = await js(
    `${panes}.findIndex((pane) => pane.getBoundingClientRect().width <= ${LEAST_PANE_WIDTH + 1})`,
  );
  assert.notEqual(narrow, -1, "A pane has the narrowest width");
  await fail(narrow);
  await check(narrow, "split-header-failed-narrow");
  await call(say, narrow, LONG);
  await check(narrow, "split-header-failed-narrow-long");
  await dismiss(narrow);
};
