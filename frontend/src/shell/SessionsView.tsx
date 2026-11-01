import { useEffect, useState } from "react";
import { useGrants } from "../access";
import type { ControlCommand, SessionSummary } from "../generated/api";
import { apiFetch } from "../api";
import { Button } from "../kit/Button";
import { ContextMenu } from "../kit/ContextMenu";
import { Checkbox } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import type { MenuEntry } from "../kit/Menu";
import { presenceColor } from "../session/Presence";
import { useCommand, useParticipants, useSession } from "../session/session";
import { ConnectDialog } from "./ConnectDialog";
import { useServerKind } from "./windowBridge";

const REFRESH_MS = 2000;
const COPIED_MS = 1500;
/** How much of a Session's id names it in the list, which has no other name to show. */
const SHORT_ID = 6;
/** Why a session has no join link: only the release build opens the listener other machines reach. */
const NO_LINK = "Join links need the release build (make build)";

/** The shared Sessions of this backend, refreshed while `enabled`; empty when there is none or the list is unavailable. */
function useSharedSessions(enabled: boolean): SessionSummary[] {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const request = new AbortController();
    const load = () =>
      apiFetch("/api/sessions", { signal: request.signal })
        .then(async (response) => {
          if (response.ok) setSessions((await response.json()) as SessionSummary[]);
        })
        // A failed refresh keeps the last list.
        .catch(() => undefined);
    void load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      request.abort();
      clearInterval(timer);
    };
  }, [enabled]);
  return enabled ? sessions : [];
}

/** Puts `text` on the clipboard; false where the page may not (an insecure origin), so the caller can fall back. */
async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** The right-click menu of a session: its join link, when this backend has one. */
const copyEntries = (link: string | null): MenuEntry[] => [
  {
    kind: "action",
    id: "copy-link",
    label: "Copy join link",
    disabled: !link,
    reason: NO_LINK,
  },
];

const copyLink = (link: string | null) => {
  if (link) void copy(link);
};

const ROW = "flex min-h-row items-center gap-m px-m";

/** The join link, selectable, with the button that copies it; the reason there is none when there is none. */
function JoinLink({ link }: { link: string | null }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <>
      {link ? (
        <input
          readOnly
          aria-label="Join link"
          value={link}
          onFocus={(event) => event.currentTarget.select()}
          className="h-row min-w-0 flex-1 rounded-control border border-line-strong bg-transparent px-m text-fg focus:border-accent focus:outline-none"
        />
      ) : (
        <span id="no-join-link" className="min-w-0 flex-1 text-muted">
          {NO_LINK}
        </span>
      )}
      <IconButton
        icon={copied ? "check" : "copy"}
        label="Copy join link"
        data-cursor-id="sessions:copy"
        aria-describedby={link ? undefined : "no-join-link"}
        disabled={!link}
        className="size-row rounded-control"
        onClick={() => {
          if (link) void copy(link).then(setCopied);
        }}
      />
    </>
  );
}

const HEADING = "m-0 px-m text-small font-semibold text-muted uppercase";

/** The sidebar's Sessions view: this window's Session and who is in it, how to connect elsewhere, and the shared Sessions to watch. */
export function SessionsView() {
  const { state, session, sharing, watch, leave } = useSession();
  const command = useCommand();
  const { you, participants } = useParticipants();
  const granted = useGrants().includes("sessions");
  const owner = state === "owner";
  const unshared = state === "none";
  const shared = useSharedSessions(granted && !owner).filter((other) => other.id !== session?.id);
  const kind = useServerKind();
  const [connecting, setConnecting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const report = (error: unknown) =>
    setFailure(error instanceof Error ? error.message : String(error));
  const control = (change: ControlCommand) =>
    command({ type: "control", control: change }).then(() => setFailure(null), report);
  const link = session?.link ?? null;

  return (
    <>
      {(granted || !unshared) && (
        <section aria-label="Session" className="flex flex-col gap-xs">
          <h3 className={HEADING}>Session</h3>
          {unshared && (
            <Button
              icon="users"
              data-cursor-id="sessions:start"
              onClick={() => void control({ type: "share", access: "watch" })}
            >
              Start sharing
            </Button>
          )}
          {owner && (
            <Button
              icon="x"
              data-cursor-id="sessions:stop"
              onClick={() => void control({ type: "stop" })}
            >
              Stop sharing
            </Button>
          )}
          {state === "watcher" && (
            <Button icon="x" data-cursor-id="sessions:leave" onClick={leave}>
              Leave
            </Button>
          )}
          {owner && (
            <>
              <div className={ROW}>
                <JoinLink link={link} />
                {link && (
                  <IconButton
                    icon="rotate-cw"
                    label="Regenerate link"
                    data-cursor-id="sessions:regenerate"
                    className="size-row rounded-control"
                    onClick={() => void control({ type: "regenerateLink" })}
                  />
                )}
              </div>
              <Checkbox
                label="Let viewers explore on their own"
                checked={sharing !== null && sharing !== "watch"}
                onChange={(explore) =>
                  void control({ type: "setAccess", access: explore ? "explore" : "watch" })
                }
                data-cursor-id="sessions:explore"
                className="min-h-row px-m"
              />
            </>
          )}
          {!unshared && (
            <ul className="m-0 flex list-none flex-col p-0">
              {participants.map((participant) => (
                <li key={participant.id} className={ROW}>
                  <span
                    aria-hidden
                    data-swatch
                    className="size-icon shrink-0 rounded-full"
                    style={{ background: presenceColor(participant.color) }}
                  />
                  <span className="min-w-0 flex-1 truncate">
                    {participant.name}
                    {participant.id === you && <span className="text-muted"> (you)</span>}
                  </span>
                  {participant.mode === "exploring" && (
                    <span className="rounded-control border border-line px-s text-small text-muted">
                      Exploring
                    </span>
                  )}
                  {participant.role === "owner" && (
                    <span role="img" aria-label="Owner" title="Owner" className="flex text-muted">
                      <Icon name="crown" />
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {unshared && kind === "local" && (
        <Button icon="plug" data-cursor-id="sessions:connect" onClick={() => setConnecting(true)}>
          Connect to server
        </Button>
      )}
      {unshared && kind === "remote" && (
        <Button
          icon="plug"
          data-cursor-id="sessions:disconnect"
          onClick={() => void window.axDevilWindow?.disconnect().catch(report)}
        >
          Disconnect
        </Button>
      )}
      {failure && (
        <p role="alert" className="m-0 px-m">
          {failure}
        </p>
      )}
      {shared.length > 0 && (
        <section aria-label="Shared sessions" className="flex flex-col gap-xs">
          <h3 className={HEADING}>Shared sessions</h3>
          <ul className="m-0 flex list-none flex-col p-0">
            {shared.map((other) => {
              const name = `Session ${other.id.slice(0, SHORT_ID)}`;
              return (
                <li key={other.id}>
                  <ContextMenu
                    entries={copyEntries(other.link ?? null)}
                    onSelect={() => copyLink(other.link ?? null)}
                  >
                    <div data-cursor-id={`session:${other.id}`} className={ROW}>
                      <span className="min-w-0 flex-1 truncate">{name}</span>
                      <span className="flex items-center gap-s text-muted" title="Participants">
                        <Icon name="users" />
                        {other.participants}
                      </span>
                      <Button
                        aria-label={`Watch ${name}`}
                        data-cursor-id={`session:${other.id}:watch`}
                        onClick={() => watch(other.id)}
                      >
                        Watch
                      </Button>
                    </div>
                  </ContextMenu>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {connecting && <ConnectDialog onClose={() => setConnecting(false)} />}
    </>
  );
}
