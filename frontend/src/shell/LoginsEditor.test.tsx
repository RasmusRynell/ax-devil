import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "../access";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { SettingsProvider, useSettings } from "../settings";
import type { Login } from "../secret";
import { LoginsEditor } from "./LoginsEditor";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("logins editor", () => {
  it("keeps the password typed while the save of the username is still in flight", async () => {
    let acknowledge = () => {};
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([]);
      const onSave = (next: Login[]) =>
        new Promise<string | null>((resolve) => {
          acknowledge = () => {
            setLogins(next);
            resolve(null);
          };
        });
      return <LoginsEditor logins={logins} onSave={onSave} />;
    }
    render(<Host />);
    fireEvent.click(screen.getByText("Add login"));
    const user = screen.getByLabelText("Username");
    fireEvent.change(user, { target: { value: "root" } });
    fireEvent.blur(user);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });

    await act(async () => acknowledge());

    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("secret");
  });

  it("keeps an edit made during a save whose answer does not tell the password", async () => {
    let acknowledge = () => {};
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([]);
      const onSave = (next: Login[]) =>
        new Promise<string | null>((resolve) => {
          acknowledge = () => {
            setLogins(next.map((login) => ({ ...login, password: { state: "literal" as const } })));
            resolve(null);
          };
        });
      return <LoginsEditor logins={logins} onSave={onSave} />;
    }
    render(<Host />);
    fireEvent.click(screen.getByText("Add login"));
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "root" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.blur(screen.getByLabelText("Password"));
    fireEvent.click(screen.getByText("Add login"));
    fireEvent.change(screen.getAllByLabelText("Username")[1]!, { target: { value: "ops" } });

    await act(async () => acknowledge());

    expect(
      screen.getAllByLabelText("Username").map((input) => (input as HTMLInputElement).value),
    ).toEqual(["root", "ops"]);
    expect((screen.getAllByLabelText("Password")[0] as HTMLInputElement).value).toBe("secret");
  });

  it("keeps an empty row added while a save is in flight", async () => {
    let acknowledge = () => {};
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([]);
      const onSave = (next: Login[]) =>
        new Promise<string | null>((resolve) => {
          acknowledge = () => {
            setLogins(next);
            resolve(null);
          };
        });
      return <LoginsEditor logins={logins} onSave={onSave} />;
    }
    render(<Host />);
    fireEvent.click(screen.getByText("Add login"));
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "root" } });
    fireEvent.blur(screen.getByLabelText("Username"));
    fireEvent.click(screen.getByText("Add login"));

    await act(async () => acknowledge());

    expect(screen.getAllByLabelText("Username")).toHaveLength(2);
  });

  it("saves a finished edit on Enter, once with the blur that follows", () => {
    const saved: Login[][] = [];
    const onSave = (next: Login[]) => {
      saved.push(next);
      return Promise.resolve(null);
    };
    render(<LoginsEditor logins={[]} onSave={onSave} />);
    fireEvent.click(screen.getByText("Add login"));
    const user = screen.getByLabelText("Username");
    fireEvent.change(user, { target: { value: "root" } });
    fireEvent.keyDown(user, { key: "Enter" });
    fireEvent.blur(user);
    expect(saved).toEqual([[{ user: "root", password: "" }]]);
  });

  it("leaves the rows as typed when an edit is refused after the user typed on", async () => {
    let refuse = (_reason: string) => {};
    const onSave = () =>
      new Promise<string | null>((resolve) => {
        refuse = resolve;
      });
    render(<LoginsEditor logins={[{ user: "root", password: "$PASS" }]} onSave={onSave} />);
    const user = screen.getByLabelText("Username") as HTMLInputElement;
    fireEvent.change(user, { target: { value: "ops" } });
    fireEvent.keyDown(user, { key: "Enter" });
    fireEvent.click(screen.getByText("Add login"));
    fireEvent.change(screen.getAllByLabelText("Username")[1]!, { target: { value: "new" } });

    await act(async () => refuse("Could not reach the backend"));

    // The user edited on since, so the rows stay as they are, with the reason shown.
    expect(
      screen.getAllByLabelText("Username").map((input) => (input as HTMLInputElement).value),
    ).toEqual(["ops", "new"]);
    expect(screen.getByRole("alert").textContent).toBe("Could not reach the backend");
  });

  it("puts a refused edit back when nothing was typed after it", async () => {
    const onSave = () => Promise.resolve("Could not reach the backend");
    render(<LoginsEditor logins={[{ user: "root", password: "$PASS" }]} onSave={onSave} />);
    const user = screen.getByLabelText("Username") as HTMLInputElement;
    fireEvent.change(user, { target: { value: "ops" } });
    await act(async () => fireEvent.keyDown(user, { key: "Enter" }));
    expect(user.value).toBe("root");
  });

  it.each([
    ["in order", [0, 1]],
    ["in reverse", [1, 0]],
  ])("puts back what the backend holds when two saves are refused %s", async (_, order) => {
    const answers: ((reason: string) => void)[] = [];
    const onSave = () =>
      new Promise<string | null>((resolve) => {
        answers.push(resolve);
      });
    render(<LoginsEditor logins={[{ user: "root", password: "$PASS" }]} onSave={onSave} />);
    const user = screen.getByLabelText("Username") as HTMLInputElement;
    fireEvent.change(user, { target: { value: "ops" } });
    fireEvent.keyDown(user, { key: "Enter" });
    fireEvent.change(user, { target: { value: "dev" } });
    fireEvent.keyDown(user, { key: "Enter" });

    await act(async () => order.forEach((at) => answers[at]!("Could not reach the backend")));

    expect(user.value).toBe("root");
  });

  it("puts back the accepted logins when a save is refused after an earlier one was accepted", async () => {
    const answers: ((reason: string | null) => void)[] = [];
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([{ user: "root", password: "$PASS" }]);
      const onSave = (next: Login[]) =>
        new Promise<string | null>((resolve) => {
          answers.push((reason) => {
            if (reason === null) setLogins(next);
            resolve(reason);
          });
        });
      return <LoginsEditor logins={logins} onSave={onSave} />;
    }
    render(<Host />);
    const user = screen.getByLabelText("Username") as HTMLInputElement;
    fireEvent.change(user, { target: { value: "ops" } });
    fireEvent.keyDown(user, { key: "Enter" });
    fireEvent.change(user, { target: { value: "dev" } });
    fireEvent.keyDown(user, { key: "Enter" });

    await act(async () => answers[0]!(null));
    await act(async () => answers[1]!("Could not reach the backend"));

    expect(user.value).toBe("ops");
  });

  it("keeps a row added and not yet filled in when the backend sends the same logins again", () => {
    const onSave = () => Promise.resolve(null);
    // The backend writes the password first; the editor's own order is user first.
    const held = () => [{ password: "$PASS", user: "root" }];
    const { rerender } = render(<LoginsEditor logins={held()} onSave={onSave} />);
    fireEvent.click(screen.getByText("Add login"));
    rerender(<LoginsEditor logins={held()} onSave={onSave} />);
    expect(screen.getAllByLabelText("Username")).toHaveLength(2);
  });

  it("saves a user added again after it was removed", async () => {
    const saved: Login[][] = [];
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([{ user: "root", password: "$PASS" }]);
      const onSave = (next: Login[]) => {
        saved.push(next);
        setLogins(next);
        return Promise.resolve(null);
      };
      return <LoginsEditor logins={logins} onSave={onSave} />;
    }
    render(<Host />);
    await act(async () => fireEvent.click(screen.getByLabelText("Remove login")));
    fireEvent.click(screen.getByText("Add login"));
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "root" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "$PASS" } });
    await act(async () => fireEvent.blur(screen.getByLabelText("Password")));
    expect(saved).toEqual([[], [{ user: "root", password: "$PASS" }]]);
  });

  it("shows a long value from its start", () => {
    render(
      <LoginsEditor
        logins={[{ user: "u".repeat(80), password: "" }]}
        onSave={() => Promise.resolve(null)}
      />,
    );
    expect((screen.getByLabelText("Username") as HTMLInputElement).selectionStart).toBe(0);
  });

  it("saves a login whose user changed without a password, so the saved one is kept", async () => {
    const saved = { ...DEFAULT_SETTINGS, revision: 1 };
    saved.values = {
      ...saved.values,
      "scan.logins": [{ id: "a1", user: "root", password: { state: "literal" } }],
    };
    const bodies: unknown[] = [];
    const fetched = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") bodies.push(JSON.parse(init.body as string));
      return { ok: true, json: async () => (url === "/api/info" ? { owner: true } : saved) };
    });
    vi.stubGlobal("fetch", fetched);
    function Logins() {
      const { values, set } = useSettings();
      return (
        <LoginsEditor
          logins={values["scan.logins"] as never}
          onSave={(next) => set("scan.logins", next).then((refusal) => refusal?.reason ?? null)}
        />
      );
    }
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
        <SettingsProvider>
          <Logins />
        </SettingsProvider>
      </AccessContext.Provider>,
    );
    const user = await screen.findByLabelText("Username");
    // The editor's mount effect, still pending on a loaded machine, would put an edit made before it ran back.
    await act(async () => {});
    fireEvent.change(user, { target: { value: "ops" } });
    fireEvent.keyDown(user, { key: "Enter" });
    await waitFor(() => expect(bodies).toHaveLength(1), { timeout: 3000 });
    expect(bodies[0]).toEqual({ set: { "scan.logins": [{ id: "a1", user: "ops" }] } });
  });
});
