import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConnectDialog } from "./ConnectDialog";
import type { WindowBridge } from "./windowBridge";

afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
});

function connectWith(connect: WindowBridge["connect"]) {
  window.axDevilWindow = { connect } as WindowBridge;
}
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "OK" }));

describe("connect dialog", () => {
  it("asks for a password only with a plain server address, passes a link through, and shows the shell's refusal", async () => {
    const connect = vi.fn<WindowBridge["connect"]>().mockResolvedValue(undefined);
    connectWith(connect);
    const onClose = vi.fn();
    render(<ConnectDialog onClose={onClose} />);
    const address = "Server address or join link";
    expect(screen.queryByLabelText("Password")).toBeNull();

    type(address, "http://host:7340");
    type("Password", "hunter2");
    type(address, "http://host:7340/#join=ann.abc");
    expect(screen.queryByLabelText("Password")).toBeNull();
    submit();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(connect).toHaveBeenLastCalledWith("http://host:7340/#join=ann.abc", undefined);

    cleanup();
    render(<ConnectDialog onClose={onClose} />);
    type(address, "http://host:7340");
    type("Password", "hunter2");
    submit();
    await waitFor(() => expect(connect).toHaveBeenLastCalledWith("http://host:7340", "hunter2"));

    cleanup();
    render(<ConnectDialog onClose={onClose} />);
    connect.mockRejectedValue(
      new Error("Error invoking remote method 'window:connect': Error: nope is not a web address."),
    );
    type(address, "nope");
    submit();
    expect((await screen.findByRole("alert")).textContent).toBe("nope is not a web address.");
  });
});
