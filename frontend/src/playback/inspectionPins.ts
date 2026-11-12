import { MAX_INSPECTION_PINS } from "../generated/api";
import { fieldKey, type FieldId } from "./fields";
import type { FieldWire } from "./protocol";

/** Pinned identities and current-sample previews. Request ordering belongs solely to Inspections. */
export class InspectionPins {
  ids: readonly FieldId[] = [];
  fields: readonly (FieldWire | null | undefined)[] = [];
  next: number | null = null;
  /** Replaces the requested identities, discarding all values until this sample is resolved. */
  set(ids: readonly FieldId[]): boolean {
    if (
      ids.length === this.ids.length &&
      ids.every((id, i) => fieldKey(id) === fieldKey(this.ids[i]))
    )
      return false;
    this.ids = ids.slice(0, MAX_INSPECTION_PINS);
    this.clear();
    return true;
  }
  /** Discards current-sample values without forgetting the user's identities. */
  clear(): void {
    this.fields = this.ids.map(() => undefined);
    this.next = this.ids.length ? 0 : null;
  }
  /** Accepts only an owner-ordered reply; undefined is pending, null is confirmed absent. */
  accept(fields: readonly (readonly [number, FieldWire])[], next: number | null): void {
    const resolved = [...this.fields];
    for (const [index, field] of fields) if (index < this.ids.length) resolved[index] ??= field;
    this.fields = next === null ? resolved.map((field) => field ?? null) : resolved;
    this.next = next;
  }
  /** Bounded digest requests; legacy saved pins use their segmented path. */
  wanted(): { group: FieldId["group"]; identity?: string; path: string[] }[] {
    return this.ids.map((id) => ({
      group: id.group,
      identity: id.identity,
      path: id.identity ? [] : [...id.path],
    }));
  }
}
