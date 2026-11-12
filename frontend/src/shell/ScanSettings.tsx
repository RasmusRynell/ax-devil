import { useState } from "react";
import { useAccess } from "../client/access";
import { reasonOf } from "../client/errors";
import type { NetworkSnapshot } from "../generated/api";
import { type SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import { FIELD } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { rescan, type ScanProblem } from "../live/network";
import { useSettings } from "../client/settings";
import { LoginsEditor } from "./LoginsEditor";
import { type Value } from "./SettingField";
import { useScanGuard } from "./useScanGuard";

const SIGN_IN_HINT =
  "Tries these logins in order on each Axis device found, to read more about it and add it with the login that worked.\nPasswords go only as HTTP Digest answers. Settings edits the same list.";

/**
 * The foot of the Live view: the settings of the network scan. The IP range with Rescan, which needs the snapshot
 * stream to be open and waits from the press until the scan it started has run, and for whoever may configure the
 * server with Full access, whether to sign in to found cameras and the logins to try. The sign-in box is this view's own choice for the scans it starts, which the `scan.sign_in`
 * setting only seeds; the logins are the setting's, saved as they are edited. The header says how the last scan went,
 * with the reason on hover, and a thin line over the top edge shows a scan running.
 */
export function ScanSettings({
  snapshot,
  epoch,
  connected,
}: {
  snapshot: NetworkSnapshot | null;
  epoch: number;
  connected: boolean;
}) {
  const { values, set } = useSettings();
  // Signing in uses the logins configured on the server, which only Full access may.
  const { fullAccess } = useAccess();
  const range = values["scan.range"];
  const stored = typeof range === "string" ? range : "";
  // The field follows the setting until the user types in it.
  const [typed, setTyped] = useState<string | null>(null);
  /** Why the last scan was refused; shown until the next one starts. */
  const [refused, setRefused] = useState<ScanProblem | null>(null);
  // The box follows the setting until the user sets it, and again whenever the setting changes.
  const setting = values["scan.sign_in"] !== false;
  const [chosen, setChosen] = useState<boolean | null>(null);
  const [seen, setSeen] = useState(setting);
  if (seen !== setting) {
    setSeen(setting);
    setChosen(null);
  }
  const signIn = fullAccess && (chosen ?? setting);
  const { scanning, press } = useScanGuard(snapshot, epoch);
  const logins = values["scan.logins"];

  /** Saves one setting; resolves to why the backend refused it, else `null`. */
  const store = (key: SettingKey, value: Value) =>
    set(key, value).then((refusal) => (refusal ? reasonOf(refusal) : null));

  const ready = connected && !scanning;
  const scan = () => {
    if (!ready) return;
    const range = typed?.trim();
    press(
      () => rescan(range === undefined || range === stored.trim() ? undefined : range, signIn),
      setRefused,
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
          <Button size="small" icon="wifi" disabled={!ready} onClick={scan}>
            Rescan
          </Button>
        </div>
        {/* Settings edits the same two, so a window too short for them leaves them out. */}
        {fullAccess && (
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
