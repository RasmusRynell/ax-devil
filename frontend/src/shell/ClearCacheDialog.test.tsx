import { afterEach, beforeAll, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { CacheCleared } from "../generated/types/api_types";
import { ClearCacheDialog } from "./ClearCacheDialog";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function clearWith(answer: CacheCleared) {
  const fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => answer }));
  vi.stubGlobal("fetch", fetch);
  const props = { onClose: vi.fn(), onCleared: vi.fn(), onFailed: vi.fn() };
  render(<ClearCacheDialog {...props} />);
  return { fetch, ...props };
}

it("leaves the cache alone on Cancel, and reports the leftovers of a partial clear as a failure", async () => {
  const cancelled = clearWith({ summary: "Removed 1 file.", failures: [] });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(cancelled.onClose).toHaveBeenCalledOnce();
  expect(cancelled.fetch).not.toHaveBeenCalled();
  cleanup();

  const partial = clearWith({ summary: "Removed 2 files.", failures: ["a.bin: busy"] });
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() =>
    expect(partial.onFailed).toHaveBeenCalledExactlyOnceWith("Removed 2 files.\na.bin: busy"),
  );
  expect(partial.onCleared).not.toHaveBeenCalled();
  expect(partial.onClose).toHaveBeenCalledOnce();
});
