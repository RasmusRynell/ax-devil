import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { useFileScopeOwner } from "./useFileScopeOwner";

describe("useFileScopeOwner", () => {
  it("gives the viewer its frames again when the seat stops following without touching playback", () => {
    const setOwner = vi.fn();
    const stream = { files: { setOwner } };
    const { rerender } = renderHook(({ following }) => useFileScopeOwner(stream, following), {
      initialProps: { following: true },
    });
    expect(setOwner).not.toHaveBeenCalled();

    rerender({ following: false });

    expect(setOwner).toHaveBeenCalledExactlyOnceWith(true);
  });
});
