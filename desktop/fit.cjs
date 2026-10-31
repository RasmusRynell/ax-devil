// Fit check run inside the page by the smoke test: returns what does not fit the window, as readable strings.
module.exports = function fit() {
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

  // Every visible shell action fits without scrolling, and nothing covers it.
  for (const control of document.querySelectorAll(
    "header button, nav[aria-label=Activity] button, aside button, aside input, main button",
  )) {
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
  for (const container of document.querySelectorAll("aside, main")) {
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
      if (element.clientWidth > 2 && element.scrollWidth > element.clientWidth + 1)
        problems.push(`cut off: "${element.textContent.trim().slice(0, 30)}"`);
    }
  }
  return problems;
};
