import type { CatalogInfoWire } from "../generated/api";
import { Checkbox, Select } from "../kit/Fields";
import { FEATURES } from "../present";
import { useSettings } from "../settings";
import { effectiveDefault, type LaneSettings, type LaneState } from "./lanes";
import { DEFAULT_CATALOG, LaneCatalogActions, USER_PREFIX } from "./LaneCatalogActions";
import { Group } from "./LaneMenuFields";

/** What the user reads for each feature. */
const FEATURE_LABELS: Record<(typeof FEATURES)[number], string> = {
  outlines: "Object outlines",
  ids: "Object IDs",
  class_names: "Class names",
  score: "Scores",
  confidence: "Confidence",
  speed: "Speed arrows",
  movement: "Movement badges",
  attributes: "Attributes",
  relations: "Relations",
};

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
}: {
  lane: LaneState;
  catalogs: readonly CatalogInfoWire[];
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  const { settings, look } = lane;
  const features = FEATURES.flatMap((feature, bit) =>
    look && (look.features & (1 << bit)) !== 0 ? [{ feature, bit }] : [],
  );
  const { values } = useSettings();
  const defaultId = effectiveDefault(catalogs, values[DEFAULT_CATALOG]);
  const entries = choices(catalogs, settings.catalog);
  return (
    <>
      <Select
        label="Catalog"
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
        usable={look !== undefined && lane.catalogProblem === undefined}
        onChange={onChange}
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
      {features.length > 0 && (
        <Group label="Features">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(18ch,1fr))] gap-x-xl gap-y-s">
            {features.map(({ feature, bit }) => (
              <Checkbox
                key={feature}
                label={FEATURE_LABELS[feature]}
                checked={(settings.hidden & (1 << bit)) === 0}
                onChange={(shown) =>
                  onChange({
                    hidden: shown ? settings.hidden & ~(1 << bit) : settings.hidden | (1 << bit),
                  })
                }
              />
            ))}
          </div>
        </Group>
      )}
    </>
  );
}
