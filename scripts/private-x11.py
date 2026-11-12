#!/usr/bin/env python3
"""Openbox and native input for the isolated Linux smoke group only."""
import ctypes as c
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

if os.environ.get("AX_DEVIL_PRIVATE_X11") != "1" or not os.environ.get("DISPLAY"):
    sys.exit("Native input requires scripts/isolated --window-manager (private Xvfb).")

try:
    x = c.CDLL("libX11.so.6")
    xt = c.CDLL("libXtst.so.6")
except OSError as error:
    sys.exit(f"Private native input needs X11 and XTest libraries: {error}")
x.XOpenDisplay.argtypes = [c.c_char_p]
x.XOpenDisplay.restype = c.c_void_p
x.XCloseDisplay.argtypes = [c.c_void_p]
x.XSync.argtypes = [c.c_void_p, c.c_int]
x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
x.XInternAtom.restype = c.c_ulong
x.XGetSelectionOwner.argtypes = [c.c_void_p, c.c_ulong]
x.XGetSelectionOwner.restype = c.c_ulong
xt.XTestFakeMotionEvent.argtypes = [c.c_void_p, c.c_int, c.c_int, c.c_int, c.c_ulong]
xt.XTestFakeButtonEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
display = x.XOpenDisplay(None)
if not display:
    sys.exit("Could not connect to the private X display.")

try:
    action, *args = sys.argv[1:]
    if action == "managed":
        if not shutil.which("openbox"):
            sys.exit("Openbox is required for native window tests; install openbox.")
        # No autostart, desktop helpers, user configuration or session bus.
        config = Path(os.environ["XDG_RUNTIME_DIR"]) / "openbox.xml"
        config.write_text('<openbox_config xmlns="http://openbox.org/3.4/rc"><focus><focusNew>yes</focusNew></focus></openbox_config>')
        wm = subprocess.Popen(["openbox", "--config-file", str(config), "--sm-disable"])
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda number, _frame: sys.exit(128 + number))
        try:
            atom = x.XInternAtom(display, b"WM_S0", False)
            deadline = time.monotonic() + 5
            while not x.XGetSelectionOwner(display, atom):
                if wm.poll() is not None or time.monotonic() >= deadline:
                    sys.exit("Openbox did not acquire the private display within 5 seconds.")
                time.sleep(0.02)
            sys.exit(subprocess.call(args))
        finally:
            if wm.poll() is None:
                wm.terminate()
                try:
                    wm.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    wm.kill()
                    wm.wait()
    elif action == "competitor":
        # A separate X client lets the WM deactivate the app without moving the pointer off its row.
        x.XDefaultRootWindow.argtypes = [c.c_void_p]
        x.XDefaultRootWindow.restype = c.c_ulong
        x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint, c.c_uint, c.c_ulong, c.c_ulong]
        x.XCreateSimpleWindow.restype = c.c_ulong
        x.XMapWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XStoreName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]
        window = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 1000, 750, 160, 120, 0, 0, 0)
        x.XStoreName(display, window, b"Private focus witness")
        x.XMapWindow(display, window)
        x.XSync(display, False)
        # The parent closes stdin in its finally block; closing the display destroys this client's window.
        sys.stdin.read()
    elif action in ("error-dialog", "focus-error-dialog"):
        x.XDefaultRootWindow.argtypes = [c.c_void_p]
        x.XDefaultRootWindow.restype = c.c_ulong
        x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
        x.XFetchName.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_char_p)]
        def find(window):
            name = c.c_char_p()
            if x.XFetchName(display, window, c.byref(name)) and name.value:
                title = name.value.decode(errors="replace")
                if "ax-devil could not continue" in title:
                    if action == "focus-error-dialog":
                        x.XRaiseWindow.argtypes = [c.c_void_p, c.c_ulong]
                        x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
                        x.XGetInputFocus.argtypes = [c.c_void_p, c.POINTER(c.c_ulong), c.POINTER(c.c_int)]
                        x.XRaiseWindow(display, window)
                        x.XSetInputFocus(display, window, 2, 0)
                        x.XSync(display, False)
                        focused, revert = c.c_ulong(), c.c_int()
                        x.XGetInputFocus(display, c.byref(focused), c.byref(revert))
                        return focused.value == window
                    return True
            root, parent, children, count = c.c_ulong(), c.c_ulong(), c.POINTER(c.c_ulong)(), c.c_uint()
            if x.XQueryTree(display, window, c.byref(root), c.byref(parent), c.byref(children), c.byref(count)):
                found = any(find(children[i]) for i in range(count.value))
                if children:
                    x.XFree(children)
                return found
            return False
        print("ready" if find(x.XDefaultRootWindow(display)) else "waiting")
    elif action == "enter":
        x.XStringToKeysym.argtypes = [c.c_char_p]
        x.XStringToKeysym.restype = c.c_ulong
        x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]
        xt.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
        key = x.XKeysymToKeycode(display, x.XStringToKeysym(b"Return"))
        xt.XTestFakeKeyEvent(display, key, True, 20)
        xt.XTestFakeKeyEvent(display, key, False, 20)
    elif action == "move":
        xt.XTestFakeMotionEvent(display, -1, int(args[0]), int(args[1]), 0)
    elif action in ("click", "double"):
        # One input transaction keeps the double-click cadence independent of process startup/load.
        for _ in range(2 if action == "double" else 1):
            xt.XTestFakeButtonEvent(display, 1, True, 20)
            xt.XTestFakeButtonEvent(display, 1, False, 20)
    elif action in ("down", "up"):
        xt.XTestFakeButtonEvent(display, 1, action == "down", 0)
    else:
        sys.exit(f"Unknown private X11 action: {action}")
    x.XSync(display, False)
finally:
    x.XCloseDisplay(display)
