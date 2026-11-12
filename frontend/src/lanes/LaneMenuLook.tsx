import type { CatalogInfoWire } from "../generated/api";
import { Button } from "../kit/Button";
import { Checkbox, Select } from "../kit/Fields";
import { FEATURES } from "../present";
import { useSettings } from "../client/settings";
import { effectiveDefault, judgedRevision, type LaneSettings, type LaneState } from "./lanes";
import { DEFAULT_CATALOG, LaneCatalogActions, USER_PREFIX } from "./LaneCatalogActions";
import { Group } from "./LaneMenuFields";
import { SECTION_HELP } from "./sectionHelp";

/** What the user reads for each feature, and what it controls. */
const FEATURE_TEXT: Record<(typeof FEATURES)[number], { label: string; description: string }> = {
  outlines: {
    label: "Object outlines",
    description: "Bounding boxes, polygons and their associated fills.",
  },
  ids: { label: "Object IDs", description: "Displayed object identifiers." },
  class_names: { label: "Class names", description: "Classification text." },
  score: { label: "Scores", description: "Score numbers and their backgrounds." },
  confidence: { label: "Confidence", description: "Confidence numbers and their backgrounds." },
  speed: { label: "Speed arrows", description: "Velocity arrows and their heads." },
  movement: { label: "Movement badges", description: "Motion-state symbols." },
  attributes: {
    label: "Attributes",
    description: "Attribute badges, color swatches and related indicators.",
  },
  relations: { label: "Relations", description: "Relationship lines and endpoint markers." },
};

/** Why a feature the catalog does not draw cannot be switched. */
const UNSUPPORTED = "This catalog does not draw this detail.";

/**
 * The catalogs the picker offers as `{id, label}`, in the order the backend sends them. A catalog with a problem is
 * offered only while the lane has it chosen, as "(invalid)"; a chosen one the backend does not list is "(missing)". A
 * user catalog whose name another catalog has too is told apart by its file name.
 */
function choices(catalogs: readonly CatalogInfoWire[], chosen: string) {
  const fileOf = (id: string) => (id.startsWith(USER_PREFIX) ? id.slice(USER_PREFIX.length) : null);
  const entries = catalogs.flatMap((catalog) => {
    const file = fileOf(catalog.id);
    if (catalog.problem !== undefined)
      return catalog.id === chosen
        ? [{ id: catalog.id, label: `${file ?? catalog.name} (invalid)` }]
        : [];
    const twin = catalogs.some((other) => other !== catalog && other.name === catalog.name);
    return [{ id: catalog.id, label: twin && file ? `${catalog.name} (${file})` : catalog.name }];
  });
  if (!catalogs.some((catalog) => catalog.id === chosen))
    entries.unshift({ id: chosen, label: `${fileOf(chosen) ?? chosen} (missing)` });
  return entries;
}

/** How the lane looks: the catalog it draws with, its theme, and which of the catalog's features show. */
export function LaneMenuLook({
  lane,
  catalogs,
  onChange,
  onReload,
}: {
  lane: LaneState;
  catalogs: readonly CatalogInfoWire[];
  onChange: (change: Partial<LaneSettings>) => void;
  onReload?: () => void;
}) {
  const { settings, look } = lane;
  const { values } = useSettings();
  const defaultId = effectiveDefault(catalogs, values[DEFAULT_CATALOG]);
  const entries = choices(catalogs, settings.catalog);
  return (
    <>
      <Select
        label="Catalog"
        help={SECTION_HELP.catalog}
        value={settings.catalog}
        options={entries.map(({ id, label }): [string, string] => [
          id,
          id === defaultId ? `${label} (default)` : label,
        ])}
        onChange={(catalog) => onChange({ catalog, theme: null })}
      />
      <LaneCatalogActions
        catalog={settings.catalog}
        label={entries.find(({ id }) => id === settings.catalog)?.label ?? settings.catalog}
        catalogs={catalogs}
        defaultId={defaultId}
        revision={judgedRevision(lane, catalogs)}
        onChange={onChange}
        onReload={onReload}
      />
      {look && look.themes.length > 0 && (
        <Select
          label="Theme"
          value={settings.theme ?? ""}
          options={[
            ["", "Default"],
            ...look.themes.map((theme): [string, string] => [theme, theme]),
          ]}
          onChange={(theme) => onChange({ theme: theme === "" ? null : theme })}
        />
      )}
      <Group label="Features">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(18ch,1fr))] gap-x-xl gap-y-s">
          {FEATURES.map((feature, bit) => {
            const supported = look !== undefined && (look.features & (1 << bit)) !== 0;
            return (
              <Checkbox
                key={feature}
                label={FEATURE_TEXT[feature].label}
                help={SECTION_HELP.details}
                title={supported ? FEATURE_TEXT[feature].description : UNSUPPORTED}
                disabled={!supported}
                checked={(settings.hidden & (1 << bit)) === 0}
                onChange={(shown) =>
                  onChange({
                    hidden: shown ? settings.hidden & ~(1 << bit) : settings.hidden | (1 << bit),
                  })
                }
              />
            );
          })}
        </div>
        <div className="flex justify-end">
          <Button disabled={settings.hidden === 0} onClick={() => onChange({ hidden: 0 })}>
            Reset
          </Button>
        </div>
      </Group>
    </>
  );
}
