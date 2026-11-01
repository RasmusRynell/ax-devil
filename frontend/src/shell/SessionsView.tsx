import { useEffect, useState } from "react";
import { useGrants } from "../access";
import type { ShareSummary } from "../generated/api";
import { apiFetch } from "../api";
import { Button } from "../kit/Button";
import { ContextMenu } from "../kit/ContextMenu";
import { Checkbox } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import type { MenuEntry } from "../kit/Menu";
import { presenceColor } from "../session/Presence";
import { useParticipants, useSession } from "../session/session";
import { ConnectDialog } from "./ConnectDialog";
import { useServerKind } from "./windowBridge";

const REFRESH_MS = 2000;
const COPIED_MS = 1500;
/** Why a session has no join link: only the release build opens the listener other machines reach. */
const NO_LINK = "Join links need the release build (make build)";

/** The sessions of this backend, refreshed while `enabled`; empty when there is none or the list is unavailable. */
function useExistingSessions(enabled: boolean): ShareSummary[] {
  const [sessions, setSessions] = useState<ShareSummary[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const request = new AbortController();
    const load = () =>
      apiFetch("/api/share/sessions", { signal: request.signal })
        .then(async (response) => {
          if (response.ok) setSessions((await response.json()) as ShareSummary[]);
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

/** The sidebar's Sessions view: this tab's session and who is in it, or how to start or watch one. */
export function SessionsView() {
  const { state, session, snapshot, create, watch, stop, leave, setExplore, regenerateLink } =
    useSession();
  const { you, participants } = useParticipants();
  const granted = useGrants().includes("sessions");
  const existing = useExistingSessions(granted && state === "none");
  const kind = useServerKind();
  const [connecting, setConnecting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (state === "none")
    return (
      <>
        {granted && (
          <Button icon="users" data-cursor-id="sessions:create" onClick={create}>
            Create session
          </Button>
        )}
        {kind === "local" && (
          <Button icon="plug" data-cursor-id="sessions:connect" onClick={() => setConnecting(true)}>
            Connect to server
          </Button>
        )}
        {kind === "remote" && (
          <Button
            icon="plug"
            data-cursor-id="sessions:disconnect"
            onClick={() =>
              void window.axDevilWindow?.disconnect().catch((error: unknown) => {
                setFailure(error instanceof Error ? error.message : String(error));
              })
            }
          >
            Disconnect
          </Button>
        )}
        {failure && (
          <p role="alert" className="m-0 px-m">
            {failure}
          </p>
        )}
        {existing.length > 0 && (
          <section aria-label="Existing sessions" className="flex flex-col gap-xs">
            <h3 className="m-0 px-m text-small font-semibold text-muted uppercase">
              Existing sessions
            </h3>
            <ul className="m-0 flex list-none flex-col p-0">
              {existing.map((other) => (
                <li key={other.id}>
                  <ContextMenu
                    entries={copyEntries(other.link)}
                    onSelect={() => copyLink(other.link)}
                  >
                    <div data-cursor-id={`session:${other.id}`} className={ROW}>
                      <span className="min-w-0 flex-1 truncate">{other.name}</span>
                      <span className="flex items-center gap-s text-muted" title="Participants">
                        <Icon name="users" />
                        {other.participants}
                      </span>
                      <Button
                        aria-label={`Watch ${other.name}`}
                        data-cursor-id={`session:${other.id}:watch`}
                        onClick={() => watch(other.id)}
                      >
                        Watch
                      </Button>
                    </div>
                  </ContextMenu>
                </li>
              ))}
            </ul>
          </section>
        )}
        {connecting && <ConnectDialog onClose={() => setConnecting(false)} />}
      </>
    );
  const owner = state === "owner";
  const link = session?.link ?? null;
  return (
    <section aria-label="This session" className="flex flex-col gap-xs">
      <ContextMenu entries={copyEntries(link)} onSelect={() => copyLink(link)}>
        <div data-cursor-id={`session:${session?.id}`} className={ROW}>
          <span className="min-w-0 flex-1 truncate font-semibold">{snapshot?.name}</span>
          <Button data-cursor-id="sessions:leave" onClick={owner ? stop : leave}>
            {owner ? "Stop sharing" : "Leave"}
          </Button>
        </div>
      </ContextMenu>
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
            {participant.role === "owner" && (
              <span role="img" aria-label="Owner" title="Owner" className="flex text-muted">
                <Icon name="crown" />
              </span>
            )}
          </li>
        ))}
      </ul>
      {owner && (
        <Checkbox
          label="Let viewers explore on their own"
          checked={snapshot?.options.explore ?? false}
          onChange={setExplore}
          data-cursor-id="sessions:explore"
        />
      )}
      {(owner || link) && (
        <div className={ROW}>
          <JoinLink link={link} />
          {owner && link && (
            <IconButton
              icon="rotate-cw"
              label="Regenerate link"
              data-cursor-id="sessions:regenerate"
              className="size-row rounded-control"
              onClick={regenerateLink}
            />
          )}
        </div>
      )}
    </section>
  );
}
