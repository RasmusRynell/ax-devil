import { useState } from "react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { type Login, LoginsEditor } from "./LoginsEditor";

afterEach(cleanup);

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

  it("keeps an edit made during a save whose answer masks the password", async () => {
    let acknowledge = () => {};
    function Host() {
      const [logins, setLogins] = useState<readonly Login[]>([]);
      const onSave = (next: Login[]) =>
        new Promise<string | null>((resolve) => {
          acknowledge = () => {
            setLogins(next.map((login) => ({ ...login, password: "********" })));
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
});
