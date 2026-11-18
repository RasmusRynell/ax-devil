import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddPlaylistDialog } from "./AddPlaylistDialog";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const reply = (json: unknown) => ({ ok: true, status: 200, json: async () => json });

it("gives the focus back to what opened it, when it closes before or after the sources arrive", async () => {
  // A browser's modal takes the focus into itself and keeps it from everything outside, so only the dialog that was
  // open when the opener had the focus can give it back.
  const focus = HTMLElement.prototype.focus;
  const spies = [
    vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement) {
      const modal = document.querySelector("dialog[open]");
      if (!modal || modal.contains(this)) focus.call(this);
    }),
    vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (
      this: HTMLDialogElement,
    ) {
      this.setAttribute("open", "");
      this.querySelector<HTMLElement>("input, select, button")?.focus();
    }),
  ];
  function Opener() {
    const [shown, setShown] = useState(false);
    return (
      <>
        <button onClick={() => setShown(true)}>Open</button>
        <SessionProvider>
          {shown && <AddPlaylistDialog onClose={() => setShown(false)} />}
        </SessionProvider>
      </>
    );
  }
  try {
    for (const [arrives, close] of [
      [true, "Cancel"],
      [true, "Escape"],
      [false, "Escape"],
    ] as const) {
      let arrive: (answer: ReturnType<typeof reply>) => void = () => {};
      const held = new Promise<ReturnType<typeof reply>>((done) => (arrive = done));
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => held),
      );
      const view = render(<Opener />);
      const opener = screen.getByRole("button", { name: "Open" });
      opener.focus();
      fireEvent.click(opener);
      await screen.findByRole("dialog");
      if (arrives) {
        arrive(reply({ decoders: [], resolvers: [] }));
        await screen.findByLabelText("Playlist source");
      }
      if (close === "Cancel") fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      else fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
      await waitFor(() => expect(document.activeElement).toBe(opener));
      view.unmount();
    }
  } finally {
    for (const spy of spies) spy.mockRestore();
  }
});
