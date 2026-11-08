// The fit check's own test, run in the page: each fixture below that is wrong must make `fit.page` report it, and the
// one valid elided label must not be reported. The widths are in `ch`, so no case depends on one platform's font. The
// fixtures are built with the DOM, since the page's Content Security Policy refuses style attributes in markup.
const assert = require("node:assert/strict");
const fit = require("./fit.cjs");
const { page } = require("./smoke-kit.cjs");

const LONG = "a long name that is far too long to fit beside anything at all, in any font";
// Wide capitals, then narrow letters: its average character is far narrower than its first two.
const WIDE = `WWW${"i".repeat(70)}`;
const ELIDED = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
const name = (style, title = LONG, shows, ellipsis, text = LONG) => ({
  tag: "span",
  style: `${ELIDED};${style}`,
  title,
  text,
  shows,
  ellipsis,
});
const row = (width, ...kids) => ({ style: `display:flex;width:${width}`, kids });
const date = { tag: "span", text: "today" };

/** A fixture as the DOM it stands for; it runs in the page. */
function make({ tag = "div", style = "", title, text, shows, ellipsis, kids = [] }) {
  const element = document.createElement(tag);
  element.style.cssText = style;
  if (title) element.title = title;
  if (text) element.textContent = text;
  if (shows) element.dataset.shows = `${shows}${ellipsis ? "…" : ""}`;
  element.append(...kids.map(make));
  return element;
}

/** Gives each element with `shows` the width of that many of its first characters, and of the ellipsis if asked. */
function size(box) {
  for (const element of box.querySelectorAll("[data-shows]")) {
    const node = document.createTreeWalker(element, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, parseInt(element.dataset.shows, 10));
    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
    probe.style.font = getComputedStyle(node.parentElement).font;
    probe.textContent = "\u2026";
    document.body.append(probe);
    const dots = element.dataset.shows.endsWith("\u2026") ? probe.getBoundingClientRect().width : 0;
    probe.remove();
    element.style.width = `${range.getBoundingClientRect().width + dots}px`;
  }
}

const block = "display:block";

/**
 * Description, fixture and, for a wrong one, the problem that must be reported.
 * @type {[string, object, RegExp | null][]}
 */
const CASES = [
  ["an elided name beside its date", row("24ch", name("flex:1;min-width:0"), date), null],
  [
    "an elided name without its text as a tooltip",
    row("24ch", name("flex:1;min-width:0", "something else"), date),
    /elided label without its text/,
  ],
  [
    "a name collapsed beside its date",
    row("24ch", name("flex:1;min-width:0;max-width:0"), date),
    /shows too little/,
  ],
  [
    "a whole row elided over its date",
    {
      title: LONG,
      style: `display:flex;width:12ch;${ELIDED}`,
      kids: [
        { tag: "span", style: "flex-shrink:0", text: LONG },
        { ...date, style: "flex-shrink:0" },
      ],
    },
    /text clipped: "today"/,
  ],
  ["a name that fits two characters", name(block, LONG, 2), /shows too little/],
  [
    "a name of wide capitals that fits two characters",
    name(block, WIDE, 2, false, WIDE),
    /shows too little/,
  ],
  ["a name that fits three characters and the ellipsis", name(block, LONG, 3, true), null],
  [
    "a name that parent hides completely",
    { style: "width:0;overflow:hidden", kids: [name(`${block};width:100px`)] },
    /shows too little/,
  ],
  [
    "a name that its parent shows two characters of",
    { style: "overflow:hidden", shows: 2, kids: [name(`${block};width:100px`)] },
    /shows too little/,
  ],
  [
    "text over its neighbour",
    {
      style: "position:relative;height:3em",
      kids: [
        { tag: "span", style: "position:absolute;left:0", text: "some words" },
        { tag: "span", style: "position:absolute;left:2ch", text: "other words" },
      ],
    },
    /text overlaps/,
  ],
  [
    "text clipped without an ellipsis",
    { style: "width:6ch;overflow:hidden;white-space:nowrap", text: LONG },
    /cut off/,
  ],
];

module.exports.fitChecker = async (window) => {
  const { js } = page(window);
  for (const [description, fixture, problem] of CASES) {
    const found = await js(`(() => {
      ${make.toString()}
      ${size.toString()}
      const box = make({ style: "position:fixed;top:0;left:0;z-index:99999", kids: [${JSON.stringify(fixture)}] });
      document.body.append(box);
      size(box);
      try { return (${fit.page.toString()})(box); } finally { box.remove(); }
    })()`);
    if (problem === null) assert.deepEqual(found, [], description);
    else
      assert.ok(
        found.some((line) => problem.test(line)),
        `${description}: ${found}`,
      );
  }
};
