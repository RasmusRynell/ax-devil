/** A rectangle in CSS pixels from the layer's top-left corner. */
export type Box = { x: number; y: number; width: number; height: number };

/** The size of an element in CSS pixels. */
export type Size = { width: number; height: number };

/**
 * Where the hover chip's top-left corner goes: just below the object's box, else beside it, else inside its bottom
 * edge, and always inside the layer. Never above the box, where the object's own id label sits.
 */
export function placeChip(box: Box, chip: Size, layer: Size): { left: number; top: number } {
  const clamp = (value: number, size: number, limit: number) =>
    Math.max(0, Math.min(value, limit - size));
  const left = clamp(box.x, chip.width, layer.width);
  const top = clamp(box.y, chip.height, layer.height);
  const roomBelow = box.y + box.height + chip.height <= layer.height;
  const roomRight = box.x + box.width + chip.width <= layer.width;
  const roomLeft = box.x - chip.width >= 0;
  if (roomBelow) return { left, top: box.y + box.height };
  if (roomRight) return { left: box.x + box.width, top };
  if (roomLeft) return { left: box.x - chip.width, top };
  return { left, top: clamp(box.y + box.height - chip.height, chip.height, layer.height) };
}
