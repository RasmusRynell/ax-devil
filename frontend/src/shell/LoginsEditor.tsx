import { useEffect, useRef, useState } from "react";
import { SETTINGS } from "../generated/settings";
import { Button } from "../kit/Button";
import { FIELD } from "../kit/look";
import { literalSecret, Refusal } from "./SettingField";

/** A login of the `scan.logins` setting: a user and a password, each as written, so `$NAME` stays a reference. */
export type Login = Record<string, string>;

const ROW = SETTINGS.find((row) => row.key === "scan.logins");

const text = (login: Login | undefined, field: "user" | "password") => login?.[field] ?? "";

/** The logins that are kept: those with a user, the user trimmed. */
const kept = (logins: readonly Login[]): Login[] =>
  logins
    .filter((login) => text(login, "user").trim() !== "")
    .map((login) => ({ user: text(login, "user").trim(), password: text(login, "password") }));

const same = (a: readonly Login[], b: readonly Login[]) => JSON.stringify(a) === JSON.stringify(b);

const literal = (password: string) => ROW !== undefined && literalSecret(ROW, password);

/** The logins without their literal passwords, which the backend answers with a mask: a save and its answer share it. */
const shape = (logins: readonly Login[]) =>
  JSON.stringify(
    kept(logins).map(({ user, password }) => [user, literal(password) ? null : password]),
  );

/** A long value is shown from its start, not its end. */
const atStart = (field: HTMLInputElement | null) => field?.setSelectionRange(0, 0);

type Props = { logins: readonly Login[] } & (
  | {
      /** Resolves to why the backend refused the logins, else `null`. */
      onSave: (logins: Login[]) => Promise<string | null>;
      onChange?: never;
    }
  | {
      /** Gets the logins after every edit; whoever holds them saves them later, as the Settings dialog does on OK. */
      onChange: (logins: Login[]) => void;
      onSave?: never;
    }
);

/**
 * The logins the scan tries, in order, a user and a password side by side; five rows show, more scroll. With `onSave`
 * a finished edit (Enter, or leaving the field) or a removal is saved at once, and a refused save puts the logins
 * back as they were, unless the user has edited on since; with `onChange` every edit is handed on and nothing is
 * saved here. A row without a user is not kept. A literal password shows masked, a `$NAME` reference as it is. Why a
 * save was refused shows under the list.
 */
export function LoginsEditor({ logins, onSave, onChange }: Props) {
  const [draft, setDraft] = useState<readonly Login[]>(logins);
  const [added, setAdded] = useState<number | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  // The logins this editor has submitted since the backend last changed on its own, by shape, each with the latest
  // submitted as written: an acknowledgement says nothing the draft does not, and the draft may have moved on since.
  const submitted = useRef(new Map<string, readonly Login[]>());
  // The logins sent last and not refused, so an Enter followed by the blur it causes sends them once.
  const sent = useRef<string | null>(null);
  // What the backend holds, and what it will once the saves in flight are accepted (null: none in flight).
  const held = useRef(logins);
  const expected = useRef<readonly Login[] | null>(null);

  // What the backend holds changed: show it, unless it is what the draft already says or what this editor saved.
  useEffect(() => {
    sent.current = null;
    held.current = logins;
    const saved = submitted.current.get(shape(logins));
    if (!saved || (expected.current && shape(expected.current) === shape(logins)))
      expected.current = null;
    if (saved) {
      // Unchanged since the save: show what the backend holds, which masks the literal passwords.
      setDraft((now) => (same(kept(now), saved) ? logins : now));
      return;
    }
    submitted.current.clear();
    setDraft((now) => (same(kept(now), logins) ? now : logins));
  }, [logins]);

  const commit = (next: readonly Login[]) => {
    const wanted = kept(next);
    const key = JSON.stringify(wanted);
    const before = expected.current;
    if (!onSave || same(wanted, before ?? logins) || sent.current === key) return;
    sent.current = key;
    expected.current = wanted;
    submitted.current.set(shape(wanted), wanted);
    void onSave(wanted).then((reason) => {
      // A newer submission is not undone by an older answer.
      if (reason !== null && sent.current === key) {
        sent.current = null;
        expected.current = before;
        // Back to what the backend holds, unless the user has edited on since.
        setDraft((now) => (same(kept(now), wanted) ? (before ?? held.current) : now));
      }
      setRefused(reason);
    });
  };
  const change = (next: readonly Login[]) => {
    setDraft(next);
    onChange?.(kept(next));
  };
  const edit = (at: number, field: "user" | "password", value: string) =>
    change(draft.map((login, index) => (index === at ? { ...login, [field]: value } : login)));
  const remove = (at: number) => {
    const next = draft.filter((_, index) => index !== at);
    change(next);
    commit(next);
  };

  return (
    <div className="flex flex-col gap-s">
      <div className="flex max-h-logins flex-col gap-s overflow-y-auto">
        {draft.map((login, at) => {
          const masked = literal(text(login, "password"));
          return (
            <div key={at} className="flex shrink-0 items-center gap-s">
              <input
                ref={atStart}
                aria-label="Username"
                placeholder="Username"
                value={text(login, "user")}
                autoFocus={added === at}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => edit(at, "user", event.target.value)}
                onBlur={() => commit(draft)}
                onKeyDown={(event) => event.key === "Enter" && commit(draft)}
                className={`${FIELD} min-w-0 flex-1 text-small`}
              />
              <input
                ref={atStart}
                aria-label="Password"
                placeholder="Password"
                type={masked ? "password" : "text"}
                value={text(login, "password")}
                autoComplete="off"
                spellCheck={false}
                // A masked password stands for the saved one; typing replaces it.
                onFocus={(event) => masked && event.currentTarget.select()}
                onChange={(event) => edit(at, "password", event.target.value)}
                onBlur={() => commit(draft)}
                onKeyDown={(event) => event.key === "Enter" && commit(draft)}
                className={`${FIELD} min-w-0 flex-1 text-small`}
              />
              <Button size="small" icon="trash-2" label="Remove login" onClick={() => remove(at)} />
            </div>
          );
        })}
      </div>
      {refused && <Refusal reason={refused} />}
      <Button
        icon="plus"
        size="small"
        className="self-start"
        onClick={() => {
          setAdded(draft.length);
          setDraft([...draft, { user: "", password: "" }]);
        }}
      >
        Add login
      </Button>
    </div>
  );
}
