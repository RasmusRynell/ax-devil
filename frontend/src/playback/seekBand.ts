/**
 * Where a run of cached frames sits on the seek bar's rail, as fractions (0 to 1) of its width: every frame owns an
 * equal bin, so the run from `first` to `final` inclusive starts at its first bin and covers one bin per frame, and a
 * single frame is as wide as one frame step.
 */
export function seekBand(
  first: number,
  final: number,
  frameCount: number,
): { from: number; span: number } {
  if (frameCount <= 0) return { from: 0, span: 0 };
  return { from: first / frameCount, span: (final - first + 1) / frameCount };
}
