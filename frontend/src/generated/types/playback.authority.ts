// Generated from playback::authority by make; do not edit.

/**
 * What a presenter says the Session's clock is doing, for one viewer. Times are microseconds.
 */
export type ClockIntent = { 
/**
 * The frame the presenter asked for.
 */
frame: number, 
/**
 * Whether the clock runs.
 */
playing: boolean, 
/**
 * Speed in hundredths: 100 is real time.
 */
speedCenti: number, 
/**
 * When the presenter stamped the intent, in server time.
 */
atUs: number, };
