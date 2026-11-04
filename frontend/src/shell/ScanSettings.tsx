import { useState } from "react";
import { useGrants } from "../access";
import { type SettingKey } from "../generated/settings";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import { FIELD } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { rescan, type ScanProblem } from "../live/network";
import { useSettings } from "../settings";
import { LoginsEditor } from "./LoginsEditor";
import { Refusal, type Value } from "./SettingField";

const SIGN_IN_HINT =
  "Tries these logins in order on each Axis device found, to read more about it and add it with the login that worked.\nPasswords go only as HTTP Digest answers. Settings edits the same list.";

/**
 * The foot of the Live view: the settings of the network scan. The IP range with Rescan, which waits while a scan
 * runs, and for whoever may configure the server, whether to sign in to found cameras and the logins to try. The
 * header says how the last scan went, with the reason on hover, and a thin line over the top edge shows a scan
 * running.
 */
export function ScanSettings({ scanning }: { scanning: boolean }) {
  const { values, shortcuts, save } = useSettings();
  const configures = useGrants().includes("configuration");
  const range = values["scan.range"];
  const stored = typeof range === "string" ? range : "";
  // The field follows the setting until the user types in it.
  const [typed, setTyped] = useState<string | null>(null);
  /** Why the last scan was refused; shown until the next one starts. */
  const [refused, setRefused] = useState<ScanProblem | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const logins = values["scan.logins"];

  /** Saves one setting; resolves to why the backend refused it, else `null`. */
  const store = (key: SettingKey, value: Value) =>
    save({ values: { ...values, [key]: value }, shortcuts }).then(
      (refusal) => refusal?.reason ?? null,
    );

  const scan = () => {
    if (scanning) return;
    const asked = typed?.trim();
    void rescan(asked === undefined || asked === stored.trim() ? undefined : asked).then(
      setRefused,
    );
  };
  const failed = scanning ? null : refused;
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
              checked={values["scan.sign_in"] === true}
              onChange={(on) => void store("scan.sign_in", on).then(setProblem)}
            />
            {values["scan.sign_in"] === true && (
              <LoginsEditor
                logins={Array.isArray(logins) ? logins : []}
                onSave={(next) => store("scan.logins", next)}
              />
            )}
          </div>
        )}
        {problem && <Refusal reason={problem} />}
      </div>
    </section>
  );
}
