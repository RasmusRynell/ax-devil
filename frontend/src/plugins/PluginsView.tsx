import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useGrants } from "../access";
import { reasonOf } from "../errors";
import type {
  HandlerKind,
  HandlerSummary,
  InstallRequest,
  PluginInfo,
  PluginsInfo,
  RemoveRequest,
  SkippedHandlerInfo,
  UpdateRequest,
} from "../generated/api";
import { BrowseButton, Button } from "../kit/Button";
import { Chip } from "../kit/Chips";
import { TextField } from "../kit/Fields";
import { SectionCaption, type CaptionMark } from "../kit/SectionCaption";
import { ask, type Answer } from "../resolvers/handlers";
import { Refusal } from "../shell/SettingField";

/** What the handlers of each kind are called in a plugin's row; a new kind is a new entry. */
const KINDS: Record<HandlerKind, string> = {
  decoder: "Decoders",
  playlist_resolver: "Playlists",
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

const post = <T,>(
  url: string,
  body: InstallRequest | RemoveRequest | UpdateRequest,
): Promise<Answer<T>> =>
  ask<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/**
 * The plugins the backend lists, fetched on mount and whenever the window gets focus; `reload` fetches again and
 * `apply` shows a newer list than any fetch still pending, which it cancels.
 */
function usePlugins() {
  const [info, setInfo] = useState<PluginsInfo | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const latest = useRef<AbortController | null>(null);
  const reload = useCallback(() => {
    latest.current?.abort();
    const request = new AbortController();
    latest.current = request;
    ask<PluginsInfo>("/api/plugins", { signal: request.signal }).then(
      (answer) => {
        if (request.signal.aborted) return;
        if (answer.ok) {
          setInfo(answer.value);
          setFailure(null);
        } else setFailure(reasonOf(answer.refusal));
      },
      () => undefined, // superseded or unmounted
    );
  }, []);
  const apply = useCallback((value: PluginsInfo) => {
    latest.current?.abort();
    setInfo(value);
  }, []);
  useEffect(() => {
    reload();
    window.addEventListener("focus", reload);
    return () => {
      window.removeEventListener("focus", reload);
      latest.current?.abort();
    };
  }, [reload]);
  return { info, apply, failure, reload };
}

/** A plugin's handlers by kind, then the file extensions its decoders read, then the ones it lists that are not used. */
function Handlers({
  handlers,
  skipped,
}: {
  handlers: readonly HandlerSummary[];
  skipped: readonly SkippedHandlerInfo[];
}) {
  const extensions = [...new Set(handlers.flatMap((handler) => handler.extensions))];
  return (
    <>
      {(Object.keys(KINDS) as HandlerKind[]).map((kind) => {
        const ofKind = handlers.filter((handler) => handler.kind === kind);
        return (
          ofKind.length > 0 && (
            <div key={kind} className="flex flex-wrap items-center gap-s">
              <span className="text-muted">{KINDS[kind]}</span>
              {ofKind.map((handler) => (
                <Chip key={handler.id} title={handler.id}>
                  {handler.name}
                </Chip>
              ))}
            </div>
          )
        );
      })}
      {extensions.length > 0 && <span className="text-muted">{extensions.join(" ")}</span>}
      {skipped.map((handler, index) => (
        <span key={index} className="text-muted [overflow-wrap:anywhere]">
          {`${handler.kind} ${handler.id ?? "-"}: ${handler.reason}`}
        </span>
      ))}
    </>
  );
}

function Row({
  plugin,
  busy,
  onChange,
}: {
  plugin: PluginInfo;
  busy: boolean;
  onChange?: (action: "update" | "remove", manifest: string) => void;
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
        <Handlers handlers={status.handlers} skipped={status.skipped} />
      ) : (
        <Refusal reason={status.reason} />
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
  const { info, apply, failure, reload } = usePlugins();
  // Installing and removing change this machine's files: only the person at it may.
  const mayChange = useGrants().includes("preferences");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  // Why the last install or remove failed; a list reload never clears it.
  const [refused, setRefused] = useState<string | null>(null);
  const bridge = window.axDevilWindow;

  const change = async (url: string, body: InstallRequest | RemoveRequest | UpdateRequest) => {
    setBusy(true);
    setRefused(null);
    try {
      const answer = await post<PluginsInfo>(url, body);
      if (answer.ok) {
        apply(answer.value);
        return true;
      }
      setRefused(reasonOf(answer.refusal));
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
              bridge && (
                <BrowseButton
                  what="folder"
                  onBrowse={() =>
                    void bridge.pickFolder().then((folder) => {
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
      {info === null && !failure && (
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
