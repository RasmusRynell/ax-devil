// Generated from api_types::playback::want by make; do not edit.

/**
 * How evenly a video's frames are spaced.
 */
export type CadenceWire = { 
/**
 * Whether more than one distinct period occurs.
 */
variable: boolean, 
/**
 * The most common periods in microseconds, most common first; at most five.
 */
periods_us: number[], };

/**
 * How a viewer steps through the video.
 */
export type Direction = "forward" | "backward";

/**
 * How the video's frame times come from its timestamps, when its container has them.
 */
export type VideoPtsWire = { 
/**
 * The stream's time base as numerator and denominator, seconds per tick.
 */
time_base: [number, number], 
/**
 * The first frame's timestamp in ticks.
 */
first_pts: number, };

/**
 * The frames a viewer holds or wants: its window around the playhead. Each want replaces the viewer's previous one;
 * the backend works on what is nearest the newest playhead first.
 */
export type Want = { 
/**
 * The sequence id the viewer shows or is about to.
 */
playhead: number, 
/**
 * How many frames after the playhead the viewer keeps.
 */
ahead: number, 
/**
 * How many frames before the playhead the viewer keeps.
 */
behind: number, 
/**
 * Which way the viewer is travelling, which decides what to prefetch first.
 */
direction: Direction, 
/**
 * How many frames after the playhead the viewer keeps Frame Maps and Sample Items for.
 */
data_ahead: number, 
/**
 * How many frames before the playhead the viewer keeps Frame Maps and Sample Items for.
 */
data_behind: number, };
