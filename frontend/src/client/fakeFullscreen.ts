import { afterEach, beforeEach, vi } from "vite-plus/test";

/** The browser's Fullscreen API on `document` and elements, which jsdom lacks, until the test ends. */
export function installFakeFullscreen() {
  const fake = { element: null as Element | null, exit: vi.fn(async () => {}) };
  const saved = {
    exit: Object.getOwnPropertyDescriptor(document, "exitFullscreen"),
    request: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "requestFullscreen"),
  };
  const enter = async (element: Element) => {
    fake.element = element;
    document.dispatchEvent(new Event("fullscreenchange"));
  };
  beforeEach(() => {
    fake.element = null;
    fake.exit.mockImplementation(async () => {
      fake.element = null;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => fake.element,
    });
    Object.defineProperty(document, "exitFullscreen", { configurable: true, value: fake.exit });
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value(this: HTMLElement) {
        return enter(this);
      },
    });
  });
  afterEach(() => {
    Reflect.deleteProperty(document, "fullscreenElement");
    for (const [target, key, original] of [
      [document, "exitFullscreen", saved.exit],
      [HTMLElement.prototype, "requestFullscreen", saved.request],
    ] as const) {
      if (original) Object.defineProperty(target, key, original);
      else Reflect.deleteProperty(target, key);
    }
  });
  return fake;
}
