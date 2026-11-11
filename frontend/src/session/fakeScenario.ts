import { readFileSync } from "node:fs";
import { join } from "node:path";
import { isDeepStrictEqual } from "node:util";
import type { Can, DocWire, EntryInfo, SeatAccess, SessionCommand } from "../generated/api";

/** Which part of a Session a Doc is projected for: the owner, or a Guest. */
export type Audience = "owner" | "guest";

/** One recorded command: sent in state `from`, the backend ends up in state `to`. */
export interface Step {
  from: string;
  command: SessionCommand;
  to: string;
}

/** A backend-written scenario: named Doc states, the ids of what they hold, and the commands that move between them. */
export interface Scenario {
  readonly name: string;
  readonly ids: Readonly<Record<string, string>>;
  readonly steps: readonly Step[];
  /** The Doc in `state` as `audience` receives it; throws naming the states it has. */
  doc(state?: string, audience?: Audience): DocWire;
}

interface Recorded {
  docs: Record<string, Record<Audience, DocWire>>;
  ids: Record<string, string>;
  steps: Step[];
}

// A path, not `new URL(.., import.meta.url)`: under jsdom, where the fake is used, that URL is not a file URL.
const FOLDER = join(import.meta.dirname, "../../../fixtures/session");

const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(FOLDER, `${name}.json`), "utf8"));

function freeze<T>(value: T): T {
  if (value && typeof value === "object") Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

const scenarios = new Map<string, Scenario>();

/** `fixtures/session/<name>.json`, as the backend wrote it. Parsed once, deep-frozen. */
export function scenario(name: string): Scenario {
  let found = scenarios.get(name);
  if (!found) {
    const { docs, ids, steps } = freeze(fixture(name) as Recorded);
    found = {
      name,
      ids,
      steps,
      doc(state = "initial", audience = "owner") {
        const at = docs[state];
        if (!at)
          throw new Error(
            `scenario "${name}" has no state "${state}"; it has ${Object.keys(docs).join(", ")}`,
          );
        return at[audience];
      },
    };
    scenarios.set(name, found);
  }
  return found;
}

/** The entries of the first item of `doc`, a playlist, as the backend resolved them. */
export function entriesOf({ workspace }: DocWire): EntryInfo[] {
  const resolution = workspace.items[0]?.resolution;
  return resolution?.status === "Resolved" && resolution.content.kind === "Playlist"
    ? resolution.content.entries
    : [];
}

let access: Readonly<Record<SeatAccess, Can>> | undefined;

/** What each kind of seat may do, as `sessions::session::can` decides it. */
export function seatAccess(): Readonly<Record<SeatAccess, Can>> {
  access ??= freeze(fixture("seat-access") as Record<SeatAccess, Can>);
  return access;
}

/** The state a fake room is in while it shows a scenario. */
export class Showing {
  constructor(
    readonly scenario: Scenario,
    public state: string,
  ) {}

  /** The Doc a seat receives, as the backend wrote it. */
  doc(owner: boolean): DocWire {
    return this.scenario.doc(this.state, owner ? "owner" : "guest");
  }

  /** Moves to the state a recorded step leads to; false when no step matches this command here. */
  step(cmd: SessionCommand): boolean {
    const hit = this.scenario.steps.find(
      ({ from, command }) => from === this.state && isDeepStrictEqual(command, cmd),
    );
    if (hit) this.state = hit.to;
    return hit !== undefined;
  }
}
