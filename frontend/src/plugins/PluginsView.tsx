import { useState, type FormEvent } from "react";
import { useAccess } from "../client/access";
import { chooseFolder, usePickPath } from "../client/pickPath";
import { ask } from "../client/ask";
import { reasonOf } from "../client/errors";
import type {
  ContributionRow,
  ErrorWire,
  FieldInfo,
  InstallRequest,
  PluginInfo,
  PluginsInfo,
  RemoveRequest,
  SettingsRequest,
  UpdateRequest,
} from "../generated/api";
import { BrowseButton, Button } from "../kit/Button";
import { Chip } from "../kit/Chips";
import { TextField } from "../kit/Fields";
import { SectionCaption, type CaptionMark } from "../kit/SectionCaption";
import { Refusal } from "../kit/Refusal";
import {
  FieldControl,
  misread,
  needsValue,
  plainForm,
  settingsOf,
  textOf,
} from "../resolvers/fields";
import type { Settings } from "../resolvers/scan";
import { usePluginListing } from "./listing";

/** What the contributions of each kind are called in a plugin's row; a new kind is a new entry. */
const KINDS: Record<string, string> = {
  decoder: "Decoders",
  playlist_resolver: "Playlists",
  task: "Tasks",
  view: "Views",
  command: "Commands",
};

/** What the buttons of an installed plugin are called, by the route they call. */
const ACTIONS = { update: "Update", remove: "Remove" } as const;

type Section = "Failed" | "Installed" | "Built-in";

const SECTIONS: readonly { heading: Section; mark: CaptionMark }[] = [
  { heading: "Failed", mark: "danger" },
  { heading: "Installed", mark: "muted" },
  { heading: "Built-in", mark: "muted" },
];

const sectionOf = ({ source, status }: PluginInfo): Section =>
  source.kind === "built_in" ? "Built-in" : status.state === "failed" ? "Failed" : "Installed";

/** What tells a plugin from the others in the list: an installed one by its manifest, a built-in by its id. */
const keyOf = ({ source, status }: PluginInfo) =>
  source.kind === "installed" ? source.manifest : status.state === "loaded" ? status.id : "";

/** The last part of a path, which names an installed plugin that did not load. */
const lastPart = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** What the user reads as the plugin's name. */
const nameOf = ({ source, status }: PluginInfo) =>
  status.state === "loaded"
    ? status.name
    : lastPart(source.kind === "installed" ? source.folder : "");

/** A plugin's contributions by kind, then the file extensions its decoders read, then the ones it lists that are not used. */
function Contributions({ rows }: { rows: readonly ContributionRow[] }) {
  const used = rows.filter((row) => row.skipped === undefined);
  const extensions = [
    ...new Set(
      used.filter((row) => row.kind === "decoder").flatMap((row) => row.detail.split(" ")),
    ),
  ].filter(Boolean);
  return (
    <>
      {Object.entries(KINDS).map(([kind, heading]) => {
        const ofKind = used.filter((row) => row.kind === kind);
        return (
          ofKind.length > 0 && (
            <div key={kind} className="flex flex-wrap items-center gap-s">
              <span className="text-muted">{heading}</span>
              {ofKind.map((row) => (
                <Chip key={row.id} title={row.id}>
                  {row.name}
                </Chip>
              ))}
            </div>
          )
        );
      })}
      {extensions.length > 0 && <span className="text-muted">{extensions.join(" ")}</span>}
      {rows
        .filter((row) => row.skipped !== undefined)
        .map((row, index) => (
          <span key={index} className="text-muted [overflow-wrap:anywhere]">
            {`${row.kind} ${row.id || "-"}: ${row.skipped}`}
          </span>
        ))}
    </>
  );
}

/** The form of a plugin's own settings, with Save while it differs from what the plugin runs with. */
function SettingsForm({
  name,
  fields,
  values,
  busy,
  onSave,
}: {
  name: string;
  fields: readonly FieldInfo[];
  values: Settings;
  busy: boolean;
  onSave: (values: Settings) => void;
}) {
  const [draft, setDraft] = useState(() => textOf(fields, values));
  const form = plainForm(draft, setDraft, usePickPath());
  const sent = settingsOf(fields, draft);
  const unchanged =
    JSON.stringify(sent) === JSON.stringify(settingsOf(fields, textOf(fields, values)));
  const incomplete = fields.some(
    (field) => misread(field, draft) || (needsValue(field) && sent[field.key] === undefined),
  );
  return (
    <form
      aria-label={`${name} settings`}
      className="flex flex-col gap-s"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(sent);
      }}
    >
      {fields.map((field) => (
        <FieldControl key={field.key} field={field} form={form} />
      ))}
      <Button
        type="submit"
        size="small"
        className="self-end"
        disabled={busy || unchanged || incomplete}
        aria-label={`Save ${name} settings`}
      >
        Save
      </Button>
    </form>
  );
}

function Row({
  plugin,
  busy,
  onChange,
  onSave,
}: {
  plugin: PluginInfo;
  busy: boolean;
  onChange?: (action: "update" | "remove", manifest: string) => void;
  onSave: (manifest: string, values: Settings) => void;
}) {
  const { source, status } = plugin;
  const installed = source.kind === "installed" ? source : null;
  const name = nameOf(plugin);
  return (
    <li data-cursor-id={`plugin:${keyOf(plugin)}`} className="flex flex-col gap-xs px-m py-xs">
      <div className="flex min-h-row items-center gap-m">
        <span title={installed?.manifest} className="min-w-0 flex-1 truncate">
          {name}
        </span>
        {status.state === "loaded" && <span className="text-muted">{status.version}</span>}
      </div>
      {status.state === "loaded" ? (
        <Contributions rows={status.rows} />
      ) : (
        <Refusal reason={status.reason} />
      )}
      {installed && status.state === "loaded" && status.settings.length > 0 && (
        <SettingsForm
          // Shows what the plugin runs with whenever that changes, such as after a save or in another window.
          key={JSON.stringify(status.values)}
          name={name}
          fields={status.settings}
          values={status.values}
          busy={busy}
          onSave={(values) => onSave(installed.manifest, values)}
        />
      )}
      {installed && onChange && (
        <div className="flex justify-end gap-s">
          {(["update", "remove"] as const).map((action) => (
            <Button
              key={action}
              size="small"
              disabled={busy}
              aria-label={`${ACTIONS[action]} ${name}`}
              data-cursor-id={`plugin:${action}:${installed.manifest}`}
              onClick={() => onChange(action, installed.manifest)}
            >
              {ACTIONS[action]}
            </Button>
          ))}
        </div>
      )}
    </li>
  );
}

/** The sidebar's Plugins view: the plugins that failed, are installed and ship with the app, and installing, updating and removing. */
export function PluginsView() {
  // `apply` shows a change's answer instead of any read still pending.
  const { value: info, error, apply, reload } = usePluginListing();
  const failure = error ? reasonOf(error as ErrorWire) : null;
  // Installing and removing run code on the server's machine: only Full access may.
  const { owner, fullAccess: mayChange } = useAccess();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  // Why the last install or remove failed; a list reload never clears it.
  const [refused, setRefused] = useState<string | null>(null);
  const pick = usePickPath();

  const change = async (
    url: string,
    body: InstallRequest | RemoveRequest | UpdateRequest | SettingsRequest,
  ) => {
    setBusy(true);
    setRefused(null);
    try {
      const answer = await ask<PluginsInfo>(url, { json: body });
      if (answer.ok) {
        apply(answer.value);
        return true;
      }
      setRefused(reasonOf(answer.error));
      // What it refused may be stale, such as a plugin another window already removed.
      reload();
    } catch (error) {
      setRefused(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
    return false;
  };
  const install = (event: FormEvent) => {
    event.preventDefault();
    void change("/api/plugins/install", { path: path.trim() }).then((installed) => {
      if (installed) setPath("");
    });
  };

  return (
    <>
      {mayChange && (
        <form onSubmit={install} className="flex flex-col gap-s px-m">
          <TextField
            label="Folder"
            value={path}
            onChange={(value) => {
              setPath(value);
              setRefused(null);
            }}
            action={
              pick && (
                <BrowseButton
                  what="folder"
                  onBrowse={() =>
                    void pick(chooseFolder()).then((folder) => {
                      if (folder !== null) setPath(folder);
                    })
                  }
                />
              )
            }
          />
          <Button
            type="submit"
            data-cursor-id="plugins:install"
            className="self-end"
            disabled={busy || path.trim() === ""}
          >
            Install
          </Button>
        </form>
      )}
      {refused && <Refusal reason={refused} />}
      {failure && <Refusal reason={failure} />}
      {info?.problem && <Refusal reason={info.problem} />}
      {owner && info === undefined && !failure && (
        <p role="status" className="m-0 px-m text-muted">
          Loading…
        </p>
      )}
      {SECTIONS.map(({ heading, mark }) => {
        const plugins = (info?.plugins ?? [])
          .filter((plugin) => sectionOf(plugin) === heading)
          .sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
        return (
          plugins.length > 0 && (
            <section key={heading} aria-label={heading} className="flex flex-col gap-xs">
              <SectionCaption heading={heading} count={plugins.length} mark={mark} />
              <ul className="m-0 flex list-none flex-col p-0">
                {plugins.map((plugin) => (
                  <Row
                    key={keyOf(plugin)}
                    plugin={plugin}
                    busy={busy}
                    onSave={(plugin, values) =>
                      void change("/api/plugins/settings", { plugin, values })
                    }
                    onChange={
                      mayChange
                        ? (action, manifest) =>
                            void change(`/api/plugins/${action}`, { target: manifest })
                        : undefined
                    }
                  />
                ))}
              </ul>
            </section>
          )
        );
      })}
    </>
  );
}
