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
