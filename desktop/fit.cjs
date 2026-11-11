// Fit checks run inside the page by the smoke test: each returns what does not fit the window, as readable strings.

/** The page with nothing open, or only the element `only`, which the checker's own test gives it. */
function page(only) {
  const problems = [];
  const root = document.documentElement;
  if (
    !only &&
    (root.scrollWidth > root.clientWidth || document.body.scrollWidth > root.clientWidth)
  )
    problems.push(`horizontal overflow: ${root.scrollWidth} > ${root.clientWidth}`);
  const inside = (rect, box) =>
    rect.left >= box.left - 1 &&
    rect.right <= box.right + 1 &&
    rect.top >= box.top - 1 &&
    rect.bottom <= box.bottom + 1;
  const viewport = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };

  // While a dialog is open the page behind it is inert, so the dialog alone is checked.
  const dialog = document.querySelector("dialog[open]");
  const controls = only
    ? []
    : dialog
      ? dialog.querySelectorAll("button, input, select")
      : document.querySelectorAll(
          "header button, nav[aria-label=Activity] button, aside button, aside input, main button",
        );
  const containers = only
    ? [only]
    : dialog
      ? [dialog]
      : document.querySelectorAll("header, aside, main");

  // Every visible shell action fits without scrolling, and nothing covers it.
  for (const control of controls) {
    const rect = control.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const name = control.ariaLabel || control.textContent;
    if (!inside(rect, viewport)) problems.push(`control outside the window: ${name}`);
    // A chip's radio or checkbox is visually hidden; its label is what the user reaches.
    if (control.matches("input.sr-only")) continue;
    const top = document.elementFromPoint(
      (rect.left + rect.right) / 2,
      (rect.top + rect.bottom) / 2,
    );
    if (!control.contains(top)) problems.push(`control covered: ${name}`);
  }

  // Text in one container never overlaps other text, no label is cut off, and no text is clipped by a box around it.
  // A label may end in an ellipsis only if it is a leaf with `text-overflow: ellipsis`, `overflow: hidden` and no
  // wrapping, has the whole text in a tooltip, and, after every box around it has clipped it, still shows its first
  // three characters and the ellipsis, as measured in its own font.
  const clippers = (start, container) => {
    const boxes = [];
    for (let up = start; up && up !== container; up = up.parentElement) {
      const style = getComputedStyle(up);
      if (style.overflowX === "hidden" || style.overflowX === "clip")
        boxes.push(up.getBoundingClientRect());
    }
    return boxes;
  };
  // A box of a pixel or two both ways hides text on purpose (a screen-reader-only label); a collapsed one does not.
  const hidesOnPurpose = (box) => box.width <= 2 && box.height <= 2;
  const ellipsisWidth = (style) => {
    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
    probe.style.fontFamily = style.fontFamily;
    probe.style.fontSize = style.fontSize;
    probe.style.fontWeight = style.fontWeight;
    probe.style.fontStyle = style.fontStyle;
    probe.style.letterSpacing = style.letterSpacing;
    probe.textContent = "…";
    document.body.append(probe);
    const { width } = probe.getBoundingClientRect();
    probe.remove();
    return width;
  };
  for (const container of containers) {
    const elided = new Set();
    for (const element of container.querySelectorAll("*")) {
      const style = getComputedStyle(element);
      const text = element.textContent.trim();
      if (
        element.childElementCount > 0 ||
        !text ||
        style.textOverflow !== "ellipsis" ||
        style.overflowX !== "hidden" ||
        !["nowrap", "pre"].includes(style.whiteSpace)
      )
        continue;
      const hides = element.scrollWidth > element.clientWidth + 1;
      // Where the text shows: the label's own content box, then every box around it.
      const own = element.getBoundingClientRect();
      const inset = element.clientLeft + parseFloat(style.paddingLeft);
      let left = own.left + inset;
      let right =
        own.left + element.clientLeft + element.clientWidth - parseFloat(style.paddingRight);
      for (const clip of clippers(element.parentElement, container)) {
        left = Math.max(left, clip.left);
        right = Math.min(right, clip.right);
      }
      const node = [...element.childNodes].find((child) => child.nodeType === Node.TEXT_NODE);
      const lead = document.createRange();
      lead.setStart(node, 0);
      lead.setEnd(node, Math.min(3, node.length));
      // What must show: the first characters, and the ellipsis where the end is cut off. A pixel of rounding is allowed.
      const needed = lead.getBoundingClientRect().width + (hides ? ellipsisWidth(style) : 0) - 1;
      const tip = element.closest("[title]")?.getAttribute("title") ?? element.ariaLabel ?? "";
      if (right - left < needed) problems.push(`label shows too little of: "${text.slice(0, 30)}"`);
      else if (hides && !tip.includes(text))
        problems.push(`elided label without its text: "${text.slice(0, 30)}"`);
      else if (hides) elided.add(element);
    }
    const boxes = [];
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent.trim();
      if (!text) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      let { left, right, top, bottom } = range.getBoundingClientRect();
      const clips = clippers(node.parentElement, container);
      for (const clip of clips) {
        if (elided.has(node.parentElement)) {
          // An elided label counts where it shows; Range measures the text that the clip hides.
          left = Math.max(left, clip.left);
          right = Math.min(right, clip.right);
        } else if (!hidesOnPurpose(clip) && (left < clip.left - 1 || right > clip.right + 1))
          problems.push(`text clipped: "${text.slice(0, 30)}"`);
      }
      const rect = { left, right, top, bottom, width: right - left, height: bottom - top };
      if (rect.width > 2 && rect.height > 2) boxes.push({ text, rect });
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
      if (element.localName === "input" || elided.has(element)) continue;
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

  // An overflow declaration alone does not prove that the content can actually be reached.
  const scrolls = (element) => {
    const before = element.scrollLeft;
    element.scrollLeft = element.scrollWidth;
    const moved = element.scrollLeft > 0;
    element.scrollLeft = before;
    return moved;
  };
  const strips = new Map();
  const tabStrip = (element) => {
    const strip = element.closest("[role=tablist]");
    if (!strip) return null;
    if (!strips.has(strip))
      strips.set(
        strip,
        /^(auto|scroll)$/.test(getComputedStyle(strip).overflowX) && scrolls(strip),
      );
    return strips.get(strip) ? strip : null;
  };
  const texts = [];
  // Every row, scrolled into view or not: no text or control sticks out of the dialog or is cut off by its own box.
  const walker = document.createTreeWalker(form, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    // Options of a closed select have no box of their own.
    const strip = tabStrip(node.parentElement);
    const tab = strip && node.parentElement.closest("[role=tab]");
    if (rect.width && rect.height && !node.parentElement.closest(".sr-only")) {
      // Compare only painted line fragments: scrolled-out text and hidden panels cannot overlap a footer.
      if (getComputedStyle(node.parentElement).visibility === "visible") {
        for (const line of range.getClientRects()) {
          const visible = {
            left: line.left,
            right: line.right,
            top: line.top,
            bottom: line.bottom,
          };
          for (
            let parent = node.parentElement;
            parent && parent !== box;
            parent = parent.parentElement
          ) {
            if (!parent.getClientRects().length) continue;
            const clip = parent.getBoundingClientRect(),
              style = getComputedStyle(parent);
            if (/auto|scroll|hidden|clip/.test(style.overflowX)) {
              visible.left = Math.max(visible.left, clip.left);
              visible.right = Math.min(visible.right, clip.right);
            }
            if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
              visible.top = Math.max(visible.top, clip.top);
              visible.bottom = Math.min(visible.bottom, clip.bottom);
            }
          }
          if (visible.right > visible.left && visible.bottom > visible.top)
            texts.push({ rect: visible, text: node.textContent.trim() });
        }
      }
      for (
        let parent = node.parentElement;
        parent && parent !== form;
        parent = parent.parentElement
      ) {
        if (parent === strip || parent.getClientRects().length === 0) continue;
        const clip = parent.getBoundingClientRect();
        if (
          (clip.width === 0 || /hidden|clip/.test(getComputedStyle(parent).overflowX)) &&
          !inside(rect, clip)
        )
          problems.push(`text clipped: "${node.textContent.trim()}"`);
      }
    }
    if (rect.width && !inside(rect, tab ? tab.getBoundingClientRect() : bounds))
      problems.push(`text outside the dialog: "${node.textContent.trim()}"`);
  }
  texts.forEach((a, index) => {
    for (const b of texts.slice(index + 1))
      if (
        Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left) > 1 &&
        Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top) > 1
      )
        problems.push(`text overlaps: "${a.text}" and "${b.text}"`);
  });
  for (const element of form.querySelectorAll("*")) {
    const rect = element.getBoundingClientRect();
    const strip = tabStrip(element);
    const tab = strip && element.closest("[role=tab]");
    const outer = tab ? tab.getBoundingClientRect() : bounds;
    if (
      tab &&
      (tab.getBoundingClientRect().width > strip.clientWidth + 1 ||
        (tab.getAttribute("aria-selected") === "true" &&
          !inside(tab.getBoundingClientRect(), strip.getBoundingClientRect())))
    )
      problems.push(`tab unreachable: ${tab.textContent}`);
    if (rect.width && !inside(rect, outer))
      problems.push(
        `outside the dialog: "${(element.ariaLabel || element.textContent).trim().slice(0, 30)}"`,
      );
    // Text inputs scroll their values; their outer bounds were checked above.
    if (
      element !== strip &&
      !(element.localName === "input" && scrolls(element)) &&
      element.clientWidth > 2 &&
      element.scrollWidth > element.clientWidth + 1
    )
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
 * A panel laid over or beside the viewer (a popover, the media tools): inside the window, not scrolling sideways, with no
 * control or text sticking out of it or cut off, and no horizontal scrollbar on the page. Height may scroll. With
 * `ellipsised`, a value cut with an ellipsis (a long id or path whose full text is its title) is not a cut-off label.
 */
function panel(selector, ellipsised = false) {
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
      !(ellipsised && getComputedStyle(element).textOverflow === "ellipsis") &&
      element.clientWidth > 2 &&
      element.scrollWidth > element.clientWidth + 1
    )
      problems.push(`cut off: "${name}"`);
  }
  return problems;
}

/**
 * The split area with several panes: every pane inside the area's contents, no two panes overlapping, and each
 * header's buttons and each seam within reach: on screen and not covered, or off screen in an area that scrolls. What
 * a viewer shows inside a pane is checked with one pane open.
 */
function split() {
  const problems = [];
  const outer = document.querySelector("main > div");
  const view = outer.getBoundingClientRect();
  const area = outer.firstElementChild.getBoundingClientRect();
  const scrolls =
    outer.scrollWidth > outer.clientWidth + 1 || outer.scrollHeight > outer.clientHeight + 1;
  const inside = (rect, box) =>
    rect.left >= box.left - 1 &&
    rect.right <= box.right + 1 &&
    rect.top >= box.top - 1 &&
    rect.bottom <= box.bottom + 1;
  const reachable = (control) => {
    const rect = control.getBoundingClientRect();
    const [x, y] = [(rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2];
    // A control whose middle is on the edge is as good as off screen: the area scrolls to it.
    if (x <= view.left || x >= view.right || y <= view.top || y >= view.bottom) return scrolls;
    return control.contains(document.elementFromPoint(x, y));
  };
  const panes = [...document.querySelectorAll('[role=group][data-cursor-id^="pane:"]')];
  const rects = panes.map((pane) => pane.getBoundingClientRect());
  panes.forEach((pane, index) => {
    const name = pane.ariaLabel;
    if (!inside(rects[index], area)) problems.push(`pane outside the area: ${name}`);
    rects.slice(index + 1).forEach((other) => {
      const across =
        Math.min(rects[index].right, other.right) - Math.max(rects[index].left, other.left);
      const down =
        Math.min(rects[index].bottom, other.bottom) - Math.max(rects[index].top, other.top);
      if (across > 1 && down > 1) problems.push(`panes overlap: ${name}`);
    });
    for (const control of pane.querySelectorAll("[draggable] button")) {
      if (!inside(control.getBoundingClientRect(), rects[index]) || !reachable(control))
        problems.push(`header button unreachable: ${control.ariaLabel}`);
    }
  });
  for (const seam of document.querySelectorAll("[role=separator][aria-label='Resize panes']")) {
    if (!inside(seam.getBoundingClientRect(), area) || !reachable(seam))
      problems.push("seam unreachable");
  }
  return problems;
}

module.exports = { page, dialog, panel, split };
