// Generated from sessions::cursors by make; do not edit.

/**
 * A position inside one element, which receivers find by its id.
 */
export type CursorElement = { 
/**
 * The element's `data-cursor-id`, at most [`CURSOR_NAME_MAX`] characters.
 */
id: string, 
/**
 * Left to right, a fraction of the element's width.
 */
x: number, 
/**
 * Top to bottom, a fraction of the element's height.
 */
y: number, };

/**
 * A cursor position. Every `x` and `y` is a fraction in `[0, 1]`, clamped by the backend, so windows of any size
 * agree.
 */
export type CursorPoint = { 
/**
 * What `x` and `y` are measured in.
 */
region: CursorRegion, 
/**
 * What the region shows right now: the sidebar view id, or the open viewer's id or `welcome`.
 */
scope: string, 
/**
 * Left to right, a fraction of the region's width.
 */
x: number, 
/**
 * Top to bottom, a fraction of the region's height.
 */
y: number, 
/**
 * The element under the pointer, when it has an id.
 */
element?: CursorElement, };

/**
 * The part of the window a cursor position belongs to.
 */
export type CursorRegion = "titleBar" | "activityBar" | "sidebar" | "main";
