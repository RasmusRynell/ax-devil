import { useState } from "react";
import { useGrants } from "../access";
import type { NetworkSnapshot } from "../generated/api";
import { type SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import { FIELD } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { rescan, type ScanProblem } from "../live/network";
import { useSettings } from "../settings";
import { LoginsEditor } from "./LoginsEditor";
import { type Value } from "./SettingField";

const SIGN_IN_HINT =
  "Tries these logins in order on each Axis device found, to read more about it and add it with the login that worked.\nPasswords go only as HTTP Digest answers. Settings edits the same list.";

/**
 * The foot of the Live view: the settings of the network scan. The IP range with Rescan, which waits from the press
 * until the scan it started has run, and for whoever may configure the server, whether to sign in to found cameras and
 * the logins to try. The sign-in box is this view's own choice for the scans it starts, which the `scan.sign_in`
 * setting only seeds; the logins are the setting's, saved as they are edited. The header says how the last scan went,
 * with the reason on hover, and a thin line over the top edge shows a scan running.
 */
export function ScanSettings({ snapshot }: { snapshot: NetworkSnapshot | null }) {
  const { values, shortcuts, save } = useSettings();
  const configures = useGrants().includes("configuration");
  const range = values["scan.range"];
  const stored = typeof range === "string" ? range : "";
  // The field follows the setting until the user types in it.
  const [typed, setTyped] = useState<string | null>(null);
  /** Why the last scan was refused; shown until the next one starts. */
  const [refused, setRefused] = useState<ScanProblem | null>(null);
  // The latest press: its scan's number, `null` until the backend answers. The scan has begun for this view once a
  // snapshot of that number or a later one is shown, which an older snapshot cannot be mistaken for.
  const [pressed, setPressed] = useState<{ scan: number | null } | null>(null);
  // The box follows the setting until the user sets it, and again whenever the setting changes.
  const setting = values["scan.sign_in"] !== false;
  const [chosen, setChosen] = useState<boolean | null>(null);
  const [seen, setSeen] = useState(setting);
  if (seen !== setting) {
    setSeen(setting);
    setChosen(null);
  }
  const signIn = chosen ?? setting;
  const waiting =
    pressed !== null && (pressed.scan === null || (snapshot?.scan ?? -1) < pressed.scan);
  const scanning = snapshot?.scanning === true || waiting;
  const logins = values["scan.logins"];

  /** Saves one setting; resolves to why the backend refused it, else `null`. */
  const store = (key: SettingKey, value: Value) =>
    save({ values: { ...values, [key]: value }, shortcuts }).then(
      (refusal) => refusal?.reason ?? null,
    );

  const scan = () => {
    if (scanning) return;
    const range = typed?.trim();
    setPressed({ scan: null });
    void rescan(range === undefined || range === stored.trim() ? undefined : range, signIn).then(
      (answer) => {
        const problem = typeof answer === "number" ? null : answer;
        setRefused(problem);
        setPressed(typeof answer === "number" ? { scan: answer } : null);
      },
    );
  };
  const reason = snapshot?.failure;
  const failed = scanning
    ? null
    : (refused ?? (reason ? { text: "Scan failed" as const, reason } : null));
  const status = scanning
    ? { text: "Scanning…" }
    : failed
      ? { text: failed.text, tone: "danger" as const, title: failed.reason }
      : null;

  return (
    <section aria-label="Scan settings" className="relative flex shrink-0 flex-col gap-xs pt-s">
      {scanning && (
        <div
          role="progressbar"
          aria-label="Scanning"
          className="absolute inset-x-0 top-0 h-xs bg-accent motion-safe:animate-pulse"
        />
      )}
      <SectionCaption
        heading="Scan settings"
        aside={status?.text}
        asideTone={status && "tone" in status ? status.tone : undefined}
        asideTitle={status && "title" in status ? status.title : undefined}
      />
      <div className="flex flex-col gap-s px-m">
        <div className="flex items-center gap-m">
          <input
            aria-label="IP range"
            placeholder="IP range, such as 192.168.1.0/24"
            value={typed ?? stored}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && scan()}
            className={`${FIELD} min-w-0 flex-1 text-small`}
          />
          <Button size="small" icon="wifi" disabled={scanning} onClick={scan}>
            Rescan
          </Button>
        </div>
        {configures && (
          // Settings edits the same two, so a window too short for them leaves them out.
          <div className="flex flex-col gap-s [@media(max-height:20rem)]:hidden">
            <Checkbox
              leading
              label="Try to sign in to found cameras"
              title={SIGN_IN_HINT}
              className="text-small"
              checked={signIn}
              onChange={setChosen}
            />
            {signIn && (
              <LoginsEditor
                logins={Array.isArray(logins) ? logins : []}
                onSave={(next) => store("scan.logins", next)}
              />
            )}
          </div>
        )}
      </div>
    </section>
  );
}
