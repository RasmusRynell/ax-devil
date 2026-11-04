import type { CatalogInfoWire } from "../generated/api";
import { Checkbox, Select } from "../kit/Fields";
import { FEATURES } from "../present";
import { useSettings } from "../settings";
import type { LaneSettings, LaneState } from "./lanes";
import { Group, MakeDefault } from "./LaneMenuFields";

const DEFAULT_CATALOG = "overlays.default_catalog";
const USER_PREFIX = "user:";

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

/** The catalogs as `[id, name]`; a user catalog whose name another catalog has too is told apart by its file name. */
function choices(catalogs: readonly CatalogInfoWire[], chosen: string): [string, string][] {
  const options = catalogs.map((catalog): [string, string] => {
    const twin = catalogs.some((other) => other !== catalog && other.name === catalog.name);
    const file = catalog.id.startsWith(USER_PREFIX) ? catalog.id.slice(USER_PREFIX.length) : null;
    return [catalog.id, twin && file ? `${catalog.name} (${file})` : catalog.name];
  });
  if (!catalogs.some((catalog) => catalog.id === chosen)) options.unshift([chosen, chosen]);
  return options;
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
  const { values } = useSettings();
  const features = FEATURES.flatMap((feature, bit) =>
    look && (look.features & (1 << bit)) !== 0 ? [{ feature, bit }] : [],
  );
  return (
    <>
      <Select
        label="Catalog"
        value={settings.catalog}
        options={choices(catalogs, settings.catalog)}
        onChange={(catalog) => onChange({ catalog, theme: null })}
      />
      <MakeDefault
        setting={DEFAULT_CATALOG}
        value={settings.catalog}
        isDefault={values[DEFAULT_CATALOG] === settings.catalog}
        usable={look !== undefined && lane.catalogProblem === undefined}
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
