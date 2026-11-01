import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { credential, GrantsContext } from "../access";
import { FakeSocket } from "./fakeSession";
import { SessionProvider } from "./session";

const disconnect = vi.fn(() => Promise.resolve());
const reload = vi.fn();

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("location", { href: window.location.href, reload });
  window.axDevilWindow = { disconnect } as never;
  FakeSocket.serverPassword = "right";
  window.sessionStorage.setItem(
    "ax-devil.credential",
    JSON.stringify({ kind: "password", password: "wrong" }),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  delete window.axDevilWindow;
});

/** A desktop window on another machine's server, which turned the stored password down. */
async function rejected() {
  render(
    <GrantsContext.Provider value={[]}>
      <SessionProvider>
        <p>app</p>
      </SessionProvider>
    </GrantsContext.Provider>,
  );
  await act(async () => {});
  return screen.findByRole("alert");
}

describe("a server that turned the password down", () => {
  it("leaves no loop: the window can return to the local backend", async () => {
    expect((await rejected()).textContent).toContain("not valid");
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("takes a new password and loads the page again with it", async () => {
    await rejected();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "秘密" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.getByText(/cannot be used/)).toBeTruthy();
    expect(reload).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(credential()).toEqual({ kind: "password", password: "right" });
    expect(reload).toHaveBeenCalledOnce();
  });
});
