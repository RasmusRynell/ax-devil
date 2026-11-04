/**
 * Turns a lane's Lane Style, catalog and data into what the presenter draws. The live viewer and the export page both
 * go through `frameOf`, so a lane looks the same on screen and in an export; the export carries the style frozen as
 * `LaneStyleWire` and never reads it any differently.
 */

import type { LaneStyleWire } from "../generated/api";
import {
  compileFilter,
  withRanges,
  type ClassMask,
  type CompiledCatalog,
  type CompiledFilter,
  type SampleView,
} from "../present";
import type { LaneFrame, LaneSettings } from "./contract";
import type { FilterOptionInfo } from "./protocol";

/** The class mask of each style, made once however many frames use it, so the presenter sees one object. */
const masks = new WeakMap<LaneStyleWire, ClassMask>();

/** The compiled frame filter of each style, made once per style object. */
const filters = new WeakMap<LaneStyleWire, CompiledFilter | null>();

/** The style's frame filter; an expression that does not compile filters nothing, as the menu applies only ones that do. */
function filterOf(style: LaneStyleWire): CompiledFilter | null {
  let filter = filters.get(style);
  if (filter === undefined) {
    const result = style.filter === null ? null : compileFilter(style.filter);
    filter = result?.ok && result.filter.test ? result.filter : null;
    filters.set(style, filter);
  }
  return filter;
}

function maskOf(style: LaneStyleWire): ClassMask {
  let mask = masks.get(style);
  if (!mask) {
    const enabled = new Uint32Array(Math.ceil(style.class_options / 32));
    for (const index of style.enabled_classes) enabled[index >> 5] |= 1 << (index & 31);
    mask = { enabled, options: style.class_options };
    masks.set(style, mask);
  }
  return mask;
}

/**
 * How the lane looks: the part of its settings that decides the pixels, with the class toggles resolved against the
 * file's filter options (`options`, from the lane's Ready status). The catalog choice is not part of it.
 */
export function styleOf(
  label: string,
  settings: LaneSettings,
  options: readonly FilterOptionInfo[],
): LaneStyleWire {
  const enabled: number[] = [];
  options.forEach((option, index) => {
    if (settings.filter[option.id] ?? option.default_enabled) enabled.push(index);
  });
  const filter = withRanges(settings.expression, settings.ranges);
  return {
    label,
    enabled_classes: enabled,
    class_options: options.length,
    hidden: settings.hidden,
    theme: settings.theme,
    retained_opacity: settings.sticky.opacity,
    filter: filter === "" ? null : filter,
  };
}

/** The sample a lane draws, with what is drawn over it. */
type FrameInputs = {
  /** The viewer's source id, the revision of its data, and the sample's index in it. */
  source: number;
  dataRevision: number;
  sample: number;
  columns: SampleView;
  catalog: CompiledCatalog;
  /** Changes whenever something that changes what is drawn changes; the presenter caches by it. */
  styleRevision: number;
  /** Whether the sample is one sticky kept past its time. */
  retained: boolean;
};

/** What the lane draws on a frame: `inputs` drawn in `style`. */
export function frameOf(style: LaneStyleWire, inputs: FrameInputs): LaneFrame {
  return {
    source: inputs.source,
    dataRevision: inputs.dataRevision,
    sample: inputs.sample,
    columns: inputs.columns,
    catalog: inputs.catalog,
    styleRevision: inputs.styleRevision,
    classMask: maskOf(style),
    filter: filterOf(style),
    hidden: style.hidden,
    theme: style.theme,
    opacity: inputs.retained ? style.retained_opacity : 1,
  };
}
