import { useEffect, useRef, useState, type FormEvent } from "react";
import { freshCatalog } from "./freshCatalog";
import { useAccess } from "../client/access";
import { reasonOf } from "../client/errors";
import type {
  CatalogInfoWire,
  CopyCatalogRequest,
  DeleteCatalogRequest,
  ReloadCatalogRequest,
} from "../generated/api";
import { Button } from "../kit/Button";
import { ChoiceDialog } from "../kit/ChoiceDialog";
import { TextField } from "../kit/Fields";
import { refusalOf } from "../client/resource";
import { ask } from "../client/ask";
import { useSettings } from "../client/settings";
import { Refusal } from "../kit/Refusal";
import type { LaneSettings } from "./lanes";

/** The Personal setting that holds the catalog new views start with. */
export const DEFAULT_CATALOG = "overlays.default_catalog";

/** What a user catalog's id starts with. */
export const USER_PREFIX = "user:";

/** What the buttons above already made the menu: the field and a refusal wrap inside it, since the menu opens placed for that width. */
const BESIDE = "w-0 min-w-full";

/** How long a new copy may stay missing from the catalog listing before the lane switches to it anyway. */
const LISTING_WAIT_MS = 3000;

/** Keys the dialog handles stay in it: the popover around it would take Escape as its own and close under the dialog. */
const keepKeys = { onKeyDown: (event: { stopPropagation: () => void }) => event.stopPropagation() };

/**
 * What the Look page does with the lane's catalog: remember it as the default for new views, copy it under a new name
 * and switch to the copy, delete it when it is a user catalog. Copy and Delete change this machine's files, so only
 * the person at it sees them. `revision` is the revision of the catalog the lane compiled, absent while it has none
 * that is current (loading, failed, deleted): then nothing is saved, copied or made the default. `label` is how the
 * picker names it.
 */
export function LaneCatalogActions({
  catalog,
  label,
  catalogs,
  defaultId,
  revision,
  onChange,
  onReload,
}: {
  catalog: string;
  label: string;
  catalogs: readonly CatalogInfoWire[];
  /** The catalog new views start with, which is also the one Delete sends the lane back to unless it is deleted. */
  defaultId: string;
  revision: number | undefined;
  onChange: (change: Partial<LaneSettings>) => void;
  /** Asks the backend for the catalog again; without it there is no Reload. */
  onReload?: () => void;
}) {
  const { values, saveCatalogDefault } = useSettings();
  const mayChange = useAccess().owner;
  const [naming, setNaming] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  // The copy the lane switches to once the listing has it: the listing arrives over the playback socket after the answer.
  const [arriving, setArriving] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const row = useRef<HTMLDivElement>(null);
  // Bumped when the lane's selector moves on: a copy asked before that no longer decides what the lane shows.
  const selection = useRef(0);
  const reloadRequest = useRef(0);
  const currentDefault = useRef(defaultId);
  currentDefault.current = defaultId;
  // What had focus (the name field, the dialog) is gone when the question is answered: the buttons take it back, so
  // the menu around them still gets Escape and Tab.
  const refocus = useRef(false);
  const stored = values[DEFAULT_CATALOG];
  const storedDefault = stored === catalog;
  // The setting that names this catalog already: an empty one means Standard, the first listed. Not the effective
  // default, which falls back to Standard when the stored one is missing, so Standard can still be saved over it.
  const savedAsDefault = storedDefault || (stored === "" && catalog === catalogs[0]?.id);
  const listed = catalogs.find((entry) => entry.id === catalog);
  const arrived = arriving !== null && catalogs.some((entry) => entry.id === arriving);
  const change = useRef(onChange);
  useEffect(() => {
    change.current = onChange;
  });

  // A form opened for one catalog must not copy another once the lane's selector moves on.
  useEffect(() => {
    selection.current += 1;
    setNaming(null);
    setArriving(null);
    setBusy(false);
    return () => {
      selection.current += 1;
    };
  }, [catalog]);

  // Switching to a copy the picker cannot name yet would show it as missing: wait for the listing, but not for ever.
  useEffect(() => {
    if (arriving === null) return;
    const go = () => {
      refocus.current = true;
      setArriving(null);
      setBusy(false);
      setNaming(null);
      change.current({ catalog: arriving, theme: null });
    };
    if (arrived) return go();
    const timer = setTimeout(go, LISTING_WAIT_MS);
    return () => clearTimeout(timer);
  }, [arriving, arrived]);

  // The field takes focus with its text selected when it opens, not again at every keystroke.
  const asking = naming !== null;
  useEffect(() => {
    const field = form.current?.querySelector("input");
    field?.focus();
    field?.select();
  }, [asking]);

  useEffect(() => {
    if (!refocus.current || !row.current) return;
    refocus.current = false;
    row.current.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
  });

  const setDefault = async (id: string) => {
    setBusy(true);
    const asked = selection.current;
    try {
      const refused = await saveCatalogDefault(id, () =>
        mayChange ? freshCatalog(id) : Promise.resolve(revision),
      );
      if (asked !== selection.current) return;
      setRefusal(refused ? reasonOf(refused) : null);
    } catch (error) {
      if (asked === selection.current) setRefusal(reasonOf(refusalOf(error)));
    } finally {
      if (asked === selection.current) setBusy(false);
    }
  };

  const finish = (failed: string | undefined, what: string) => {
    refocus.current = true;
    setBusy(false);
    setRefusal(failed === undefined ? null : `Could not ${what} the catalog: ${failed}`);
  };

  // The backend reads the file again first (an edit the folder polls cannot see is read now), then the lane asks for
  // the document anew.
  const reread = (refresh: () => void) => {
    setRefusal(null);
    const asked = selection.current;
    const request = ++reloadRequest.current;
    const body: ReloadCatalogRequest = { id: catalog };
    void ask("POST /api/catalogs/reload", { request: body }).then((answer) => {
      if (asked !== selection.current || request !== reloadRequest.current) return;
      refresh();
      if (!answer.ok && mayChange)
        setRefusal(`Could not reload the catalog: ${reasonOf(answer.error)}`);
    });
  };

  const copy = (event: FormEvent) => {
    event.preventDefault();
    if (naming === null || revision === undefined) return;
    setBusy(true);
    setRefusal(null);
    const asked = selection.current;
    const request: CopyCatalogRequest = { source: catalog, revision, name: naming.trim() };
    void ask("POST /api/catalogs/copy", { request }).then((answer) => {
      if (asked !== selection.current) return;
      if (!answer.ok) return finish(reasonOf(answer.error), "create");
      setArriving(answer.value.id);
    });
  };

  const remove = () => {
    refocus.current = true;
    setConfirming(false);
    setBusy(true);
    setRefusal(null);
    const asked = selection.current;
    const request: DeleteCatalogRequest = { id: catalog };
    void ask("POST /api/catalogs/delete", { request }).then((answer) => {
      if (!answer.ok) {
        if (asked === selection.current) finish(reasonOf(answer.error), "delete");
        return;
      }
      // The default goes back to Standard, which the backend lists first; this lane follows the new default unless
      // its selector moved on meanwhile.
      if (asked === selection.current) {
        change.current({
          catalog:
            currentDefault.current === catalog ? (catalogs[0]?.id ?? "") : currentDefault.current,
          theme: null,
        });
        refocus.current = true;
        setBusy(false);
      }
    });
  };

  return (
    <>
      {naming === null && (
        <div ref={row} className="flex flex-wrap items-start gap-s">
          <Button
            disabled={revision === undefined || savedAsDefault || busy}
            onClick={() => void setDefault(catalog)}
          >
            Use as default
          </Button>
          {onReload && <Button onClick={() => reread(onReload)}>Reload</Button>}
          {mayChange && (
            <Button
              disabled={revision === undefined || busy}
              onClick={() => setNaming(`${listed?.name ?? label} copy`)}
            >
              Copy
            </Button>
          )}
          {mayChange && listed && catalog.startsWith(USER_PREFIX) && (
            <Button disabled={busy} onClick={() => setConfirming(true)}>
              Delete
            </Button>
          )}
        </div>
      )}
      {mayChange && naming !== null && (
        <form ref={form} onSubmit={copy} className={`${BESIDE} flex flex-col items-start gap-s`}>
          <TextField label="Name" value={naming} onChange={setNaming} />
          <div className="flex gap-s">
            <Button type="submit" disabled={busy || naming.trim() === ""}>
              Create
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                refocus.current = true;
                setNaming(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
      {refusal && (
        <div className={BESIDE}>
          <Refusal reason={refusal} />
        </div>
      )}
      {confirming && (
        <div className="contents" {...keepKeys}>
          <ChoiceDialog
            title="Delete Catalog"
            message={`Delete ${label}? The file is deleted permanently.${
              storedDefault
                ? " It is the default catalog, so new views will start from Standard."
                : ""
            }`}
            choices={[
              { id: "delete", label: "Delete" },
              { id: "cancel", label: "Cancel" },
            ]}
            defaultId="cancel"
            onChoose={(choice) => {
              if (choice === "delete") return remove();
              refocus.current = true;
              setConfirming(false);
            }}
          />
        </div>
      )}
    </>
  );
}
