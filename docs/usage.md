# Using ax-devil

- Export (video with overlays, or a PNG of one frame) runs on the machine that runs the backend and needs Electron
  there; it needs no display. A window on another machine types a path on that server. It draws on the graphics card
  when it can, else in software; `AX_DEVIL_RENDER_GRAPHICS=software` starts in software.
