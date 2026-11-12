// jsdom does no layout and has no ResizeObserver; code that reads layout only needs the API to exist. Suites that
// run in the node environment have neither jsdom nor these.
if (typeof document !== "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };

  // jsdom does not implement modal dialogs.
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
}
