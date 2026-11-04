// Fit checks run inside the page by the smoke test: each returns what does not fit the window, as readable strings.

/** The page with nothing open. */
function page() {
  const problems = [];
  const root = document.documentElement;
  if (root.scrollWidth > root.clientWidth || document.body.scrollWidth > root.clientWidth)
    problems.push(`horizontal overflow: ${root.scrollWidth} > ${root.clientWidth}`);
  const inside = (rect, box) =>
    rect.left >= box.left - 1 &&
    rect.right <= box.right + 1 &&
    rect.top >= box.top - 1 &&
    rect.bottom <= box.bottom + 1;
  const viewport = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };

  // While a dialog is open the page behind it is inert, so the dialog alone is checked.
  const dialog = document.querySelector("dialog[open]");
  const controls = dialog
    ? dialog.querySelectorAll("button, input, select")
    : document.querySelectorAll(
        "header button, nav[aria-label=Activity] button, aside button, aside input, main button",
      );
  const containers = dialog ? [dialog] : document.querySelectorAll("header, aside, main");

  // Every visible shell action fits without scrolling, and nothing covers it.
  for (const control of controls) {
    const rect = control.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const name = control.ariaLabel || control.textContent;
    if (!inside(rect, viewport)) problems.push(`control outside the window: ${name}`);
    const top = document.elementFromPoint(
      (rect.left + rect.right) / 2,
      (rect.top + rect.bottom) / 2,
    );
    if (!control.contains(top)) problems.push(`control covered: ${name}`);
  }

  // Text in one container never overlaps other text, and no label is cut off.
  for (const container of containers) {
    const boxes = [];
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      if (rect.width > 2 && rect.height > 2) boxes.push({ text: node.textContent.trim(), rect });
    }
    boxes.forEach((a, index) =>
      boxes.slice(index + 1).forEach((b) => {
        const across = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        const down = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (across > 1 && down > 1) problems.push(`text overlaps: "${a.text}" and "${b.text}"`);
      }),
    );
    for (const element of container.querySelectorAll("*")) {
      // An input scrolls a value longer than its field, such as a join link; that is not a cut-off label.
      if (element.localName === "input") continue;
      if (element.clientWidth > 2 && element.scrollWidth > element.clientWidth + 1)
        problems.push(`cut off: "${element.textContent.trim().slice(0, 30)}"`);
    }
  }
  return problems;
}

/**
 * The open dialog: it must fit the window without scrolling itself (a list inside it may scroll), and its title,
 * labels and controls must not be clipped sideways, so the footer buttons stay reachable.
 */
function dialog() {
  const problems = [];
  const box = document.querySelector("dialog[open]");
  const form = box.querySelector("form");
  const bounds = box.getBoundingClientRect();
  if (
    bounds.left < -1 ||
    bounds.right > innerWidth + 1 ||
    bounds.top < -1 ||
    bounds.bottom > innerHeight + 1
  )
    problems.push("dialog outside the window");
  if (box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1)
    problems.push("dialog scrolls itself");
  const inside = (rect, outer) => rect.left >= outer.left - 1 && rect.right <= outer.right + 1;

  // Every row, scrolled into view or not: no text or control sticks out of the dialog or is cut off by its own box.
  const walker = document.createTreeWalker(form, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    // Options of a closed select have no box of their own.
    if (rect.width && !inside(rect, bounds))
      problems.push(`text outside the dialog: "${node.textContent.trim()}"`);
  }
  for (const element of form.querySelectorAll("*")) {
    const rect = element.getBoundingClientRect();
    if (rect.width && !inside(rect, bounds))
      problems.push(
        `outside the dialog: "${(element.ariaLabel || element.textContent).trim().slice(0, 30)}"`,
      );
    if (element.clientWidth > 2 && element.scrollWidth > element.clientWidth + 1)
      problems.push(`cut off: "${element.textContent.trim().slice(0, 30)}"`);
  }

  // The footer buttons (the form's last child) are on screen and nothing covers them.
  for (const button of form.lastElementChild.querySelectorAll("button")) {
    const rect = button.getBoundingClientRect();
    const top = document.elementFromPoint(
      (rect.left + rect.right) / 2,
      (rect.top + rect.bottom) / 2,
    );
    if (rect.bottom > innerHeight + 1 || !button.contains(top))
      problems.push(`footer button unreachable: ${button.textContent}`);
  }
  return problems;
}

/**
 * A panel laid over or beside the viewer (a popover, the inspector): inside the window, not scrolling sideways, with no
 * control or text sticking out of it or cut off, and no horizontal scrollbar on the page. Height may scroll.
 */
function panel(selector) {
  const problems = [];
  const box = document.querySelector(selector);
  if (!box) return [`no panel: ${selector}`];
  const bounds = box.getBoundingClientRect();
  if (
    bounds.left < -1 ||
    bounds.right > innerWidth + 1 ||
    bounds.top < -1 ||
    bounds.bottom > innerHeight + 1
  )
    problems.push(
      `panel outside the window: ${[bounds.left, bounds.top, bounds.right, bounds.bottom].map(Math.round).join()}`,
    );
  if (box.scrollWidth > box.clientWidth + 1) problems.push("panel scrolls sideways");
  const root = document.documentElement;
  if (root.scrollWidth > root.clientWidth) problems.push("the page scrolls sideways");
  for (const element of box.querySelectorAll("*")) {
    const rect = element.getBoundingClientRect();
    const name = (element.ariaLabel || element.textContent).trim().slice(0, 30);
    if (rect.width && (rect.left < bounds.left - 1 || rect.right > bounds.right + 1))
      problems.push(`outside the panel: "${name}"`);
    // A table cell that ends in an ellipsis is elided on purpose, and its whole text is its tooltip.
    const elided =
      element.closest("[role=option]") && getComputedStyle(element).textOverflow === "ellipsis";
    if (
      element.localName !== "input" &&
      !elided &&
      element.clientWidth > 2 &&
      element.scrollWidth > element.clientWidth + 1
    )
      problems.push(`cut off: "${name}"`);
  }
  return problems;
}

module.exports = { page, dialog, panel };
