import { useRef, useState } from "react";
import { credential, useAccess } from "../client/access";
import { Icon } from "../kit/Icon";
import { QUIET } from "../kit/look";
import { Menu, type MenuEntry } from "../kit/Menu";
import { Popup } from "../kit/Popup";
import { presenceColor } from "../session/Presence";
import { useCommand, useParticipants, useSession } from "../session/session";

/** How many participants show as avatars; the rest are counted. */
const AVATARS = 3;

/** The link that brings a browser's own join link into the desktop app. */
const appLink = (link: string) => `ax-devil://open?url=${encodeURIComponent(link)}`;

/** The join link this page can open in the app: the session's own, or the one a link Guest arrived with. */
function openableLink(own: string | null): string | null {
  if (own) return own;
  const held = credential();
  if (held?.kind !== "join") return null;
  const { origin, pathname } = window.location;
  return `${origin}${pathname}#join=${encodeURIComponent(`${held.session}.${held.secret}`)}`;
}

/**
 * The title bar's share entry. Not shared: "Start sharing". Shared, or a Guest: who is in the Session, for a
 * Guest whose presentation it is and, while exploring, the way back to it, and a menu with the two cursor options
 * and the way out. Everything here belongs to this window alone, so it works for a Guest too.
 */
export function ShareControl({ onStartSharing }: { onStartSharing: () => void }) {
  const { state, session, mode, cursorOptions, setCursorOptions, leave, follow } = useSession();
  const command = useCommand();
  const { participants } = useParticipants();
  const sessions = useAccess().owner;
  const [open, setOpen] = useState(false);
  const boundary = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const wrap = "flex shrink-0 items-center gap-s px-s [-webkit-app-region:no-drag]";

  if (state === "none")
    return sessions ? (
      <div className={wrap}>
        <button type="button" data-cursor-id="share" className={QUIET} onClick={onStartSharing}>
          <Icon name="users" />
          <span className="max-sm:sr-only">Start sharing</span>
        </button>
      </div>
    ) : null;

  const owner = state === "owner";
  const exploring = participants.filter((participant) => participant.mode === "exploring").length;
  const watching = participants.filter((participant) => participant.role === "guest").length;
  const presenter = participants.find((participant) => participant.role === "owner");
  const inApp = window.axDevilWindow ? null : openableLink(session?.link ?? null);
  const openInApp: MenuEntry[] = inApp
    ? [{ kind: "action", id: "open-in-app", label: "Open in app" }]
    : [];
  const entries: MenuEntry[] = [
    { kind: "check", id: "show", label: "Show others' cursors" },
    { kind: "check", id: "share", label: "Share my cursor" },
    { kind: "separator" },
    ...openInApp,
    { kind: "action", id: "leave", label: owner ? "Stop sharing" : "Leave" },
  ];
  const checked = new Set([
    ...(cursorOptions.show ? ["show"] : []),
    ...(cursorOptions.share ? ["share"] : []),
  ]);
  const select = (id: string) => {
    if (id === "show") setCursorOptions({ ...cursorOptions, show: !cursorOptions.show });
    else if (id === "share") setCursorOptions({ ...cursorOptions, share: !cursorOptions.share });
    else {
      setOpen(false);
      if (id === "open-in-app" && inApp) window.location.assign(appLink(inApp));
      else if (owner)
        void command({ type: "control", control: { type: "stop" } }).catch(() => undefined);
      else leave();
    }
  };
  return (
    <div className={wrap}>
      {!owner && presenter && (
        <span
          data-cursor-id="share:presenter"
          className="whitespace-nowrap text-muted max-sm:hidden"
        >
          Presenting: <span className="text-fg">{presenter.name}</span>
        </span>
      )}
      {!owner && mode === "exploring" && (
        <button
          type="button"
          data-cursor-id="share:follow"
          className={QUIET}
          onClick={() => void follow().catch(() => undefined)}
        >
          <Icon name="eye" />
          <span className="max-sm:sr-only">Follow presenter</span>
        </button>
      )}
      <div ref={boundary} className="relative">
        <button
          ref={trigger}
          type="button"
          aria-label="Session"
          title={exploring > 0 ? `${watching} watching, ${exploring} exploring` : "Session"}
          aria-haspopup="menu"
          aria-expanded={open}
          data-cursor-id="share"
          onClick={() => setOpen(!open)}
          className={`${QUIET} gap-s aria-expanded:bg-hover`}
        >
          <span aria-hidden className="flex items-center">
            {participants.slice(0, AVATARS).map((participant) => (
              <span
                key={participant.id}
                title={participant.name}
                className="-ml-s size-icon rounded-full border border-panel first:ml-0"
                style={{ background: presenceColor(participant.color) }}
              />
            ))}
            {participants.length > AVATARS && (
              <span className="ml-s text-small text-muted">+{participants.length - AVATARS}</span>
            )}
          </span>
          {exploring > 0 && (
            <span
              aria-hidden
              className="flex items-center gap-xs text-small text-muted max-sm:hidden"
            >
              <Icon name="eye" />
              {exploring}
            </span>
          )}
          <Icon name="chevron-down" />
        </button>
        {open && (
          <Popup
            boundary={boundary}
            onClose={() => setOpen(false)}
            className="absolute top-full right-0 z-10 min-w-max"
          >
            <Menu
              entries={entries}
              checked={checked}
              onSelect={(command) => {
                select(command.id);
                if (command.kind === "action") trigger.current?.focus();
              }}
            />
          </Popup>
        )}
      </div>
    </div>
  );
}
