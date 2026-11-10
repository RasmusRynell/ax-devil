// Generated from api_types::window by make; do not edit.

/**
 * Where a window was and how big: the body of `GET` and `PUT /api/windows/{name}`.
 */
export type WindowState = { 
/**
 * Left edge of the window when not maximized, in screen pixels.
 */
x: number, 
/**
 * Top edge of the window when not maximized, in screen pixels.
 */
y: number, 
/**
 * Width of the window when not maximized.
 */
width: number, 
/**
 * Height of the window when not maximized.
 */
height: number, 
/**
 * Whether the window was maximized.
 */
maximized: boolean, };
