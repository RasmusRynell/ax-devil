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

/**
 * The logins the scan tries, in order, a user and a password side by side; five rows show, more scroll. A finished edit
 * (Enter, or leaving the field) or a removal is saved at once; a row without a user is not kept. A literal password shows masked, a `$NAME`
 * reference as it is. Why a save was refused shows under the list.
 */
export function LoginsEditor({
  logins,
  onSave,
}: {
  logins: readonly Login[];
  /** Resolves to why the backend refused the logins, else `null`. */
  onSave: (logins: Login[]) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState<readonly Login[]>(logins);
  const [added, setAdded] = useState<number | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  // The logins this editor has submitted since the backend last changed on its own: their acknowledgements say
  // nothing the draft does not, and the draft may have moved on since.
  const submitted = useRef(new Set<string>());
  // The logins sent last, so an Enter followed by the blur it causes sends them once.
  const sent = useRef<string | null>(null);

  // What the backend holds changed: show it, unless it is what the draft already says or what this editor saved.
  useEffect(() => {
    sent.current = null;
    if (submitted.current.has(JSON.stringify(logins))) return;
    submitted.current.clear();
    setDraft((now) => (same(kept(now), logins) ? now : logins));
  }, [logins]);

  const commit = (next: readonly Login[]) => {
    const wanted = kept(next);
    if (same(wanted, logins) || sent.current === JSON.stringify(wanted)) return;
    sent.current = JSON.stringify(wanted);
    submitted.current.add(JSON.stringify(wanted));
    void onSave(wanted).then(setRefused);
  };
  const edit = (at: number, field: "user" | "password", value: string) =>
    setDraft(draft.map((login, index) => (index === at ? { ...login, [field]: value } : login)));
  const remove = (at: number) => {
    const next = draft.filter((_, index) => index !== at);
    setDraft(next);
    commit(next);
  };

  return (
    <div className="flex flex-col gap-s">
      <div className="flex max-h-logins flex-col gap-s overflow-y-auto">
        {draft.map((login, at) => {
          const masked = ROW !== undefined && literalSecret(ROW, text(login, "password"));
          return (
            <div key={at} className="flex shrink-0 items-center gap-s">
              <input
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
              <Button
                size="small"
                icon="trash-2"
                label="Remove this login"
                onClick={() => remove(at)}
              />
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
