import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { GrantsContext } from "../access";
import { FakeSocket } from "../session/fakeBackend";
import { SessionProvider } from "../session/session";
import { SessionsView } from "./SessionsView";
import { ShareControl } from "./ShareControl";

const writeText = vi.fn(async () => {});

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => [
        { id: "ann", name: "Ann", ownerPresent: true, participants: 1, link: null, mine: false },
      ],
    })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  writeText.mockClear();
});

describe("sessions view", () => {
  it("creates a session, copies its join link, lets viewers explore, and stopping makes the window private again", async () => {
    render(
      <GrantsContext value={["sessions"]}>
        <SessionProvider>
          <ShareControl onShare={() => {}} />
          <SessionsView />
        </SessionProvider>
      </GrantsContext>,
    );
    expect(await screen.findByRole("button", { name: "Watch Ann" })).toBeTruthy();
    expect(FakeSocket.sockets).toHaveLength(0);
    // Connecting to a server is the desktop shell's: a plain browser is not offered it.
    expect(screen.queryByRole("button", { name: "Connect to server" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create session" }));
    const session = within(await screen.findByRole("region", { name: "This session" }));
    expect(session.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Guest 1 (you)",
    ]);
    const link = (session.getByLabelText("Join link") as HTMLInputElement).value;
    expect(link).toContain("#join=");
    fireEvent.click(session.getByRole("button", { name: "Copy join link" }));
    expect(writeText).toHaveBeenCalledWith(link);

    // Viewers explore only while the owner allows it; the server's snapshot is what the box shows.
    fireEvent.click(session.getByRole("checkbox", { name: "Let viewers explore on their own" }));
    await act(async () => {});
    expect(FakeSocket.rooms.get("room-1")?.explore).toBe(true);
    expect((session.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);

    // The cursor option is this person's own, sent to the backend.
    fireEvent.click(screen.getByRole("button", { name: "Session" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Share my cursor" }));
    expect(FakeSocket.latest.received).toContainEqual({
      type: "cursorOptions",
      show: true,
      share: false,
    });

    // A backend other machines cannot reach has no link, and says why.
    const welcome = FakeSocket.latest;
    act(() =>
      welcome.emit({
        type: "welcome",
        session: "room-1",
        you: 1,
        role: "owner",
        resume: "r",
        link: null,
      }),
    );
    expect(session.getByRole("button", { name: "Copy join link" }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(session.getByText("Join links need the release build (make build)")).toBeTruthy();

    fireEvent.click(session.getByRole("button", { name: "Stop sharing" }));
    expect(await screen.findByRole("button", { name: "Create session" })).toBeTruthy();
  });
});
