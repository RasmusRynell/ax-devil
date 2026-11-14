// Generated from sessions::session::layout by make; do not edit.

/**
 * Which way a split divides its space.
 */
export type Axis = "row" | "column";

/**
 * Where each viewer is: a pane, or a split of two layouts.
 */
export type Layout = { "type": "pane", 
/**
 * The viewer.
 */
viewer: number, } | { "type": "split", 
/**
 * Which way it divides.
 */
axis: Axis, 
/**
 * The first child's part, from 1 to 9999 of every 10000.
 */
share: number, 
/**
 * The left or upper child.
 */
first: Layout, 
/**
 * The right or lower child.
 */
second: Layout, };
