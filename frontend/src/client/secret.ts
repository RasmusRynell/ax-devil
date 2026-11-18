import type { SecretState } from "../generated/api";

/** Whether the whole value is a `$NAME` reference to an environment variable. */
export const isReference = (value: string) => /^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value);

/** Whether *value* is what the backend tells of a secret that is not being edited: whether it is set, never its value. */
export const isSecretState = (value: unknown): value is SecretState =>
  typeof value === "object" && value !== null && !Array.isArray(value) && "state" in value;

/** The state of the secret *text*, as the backend reports it once it is saved. */
export const secretStateOf = (text: string, env: boolean): SecretState =>
  text === ""
    ? { state: "unset" }
    : env && isReference(text)
      ? { state: "reference", name: text.slice(1) }
      : { state: "literal" };

/**
 * A login of the `scan.logins` setting: a user as written, so `$NAME` stays a reference, and a password that is either
 * what the backend says of the saved one (never the password itself) or the text typed to replace it. A login without
 * an `id` is new.
 */
export interface Login {
  id?: string;
  user: string;
  password: SecretState | string;
}
