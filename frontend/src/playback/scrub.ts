/** Whether the seek bar is held, and whether the clock ran when it was grabbed. */
export class Scrub {
  #ran: boolean | undefined;

  /** The bar is grabbed while the clock is `playing`; reports whether that clock must pause (false if the bar was already held). */
  grab(playing: boolean): boolean {
    if (this.#ran !== undefined) return false;
    this.#ran = playing;
    return playing;
  }

  /** The bar is let go; reports whether the clock ran when it was grabbed. */
  release(): boolean {
    const ran = this.#ran === true;
    this.#ran = undefined;
    return ran;
  }
}
