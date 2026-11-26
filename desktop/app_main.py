# The start of the Mac and Windows apps (see build.sh and the Windows workflow). The program is the same as
# everywhere else; this only chooses where the user's files go, the touch layout, and a window size.
import os
import sys
import traceback

home = os.path.join(os.path.expanduser("~"), "Music", "PocketPads")
os.makedirs(home, exist_ok=True)
os.environ.setdefault("PP_DATA", home)
home = os.environ["PP_DATA"]
os.environ.setdefault("PP_UI", "tablet")        # every control can be clicked; PP_UI=handheld gives the keyboard layout
if "PP_WINDOW" not in os.environ:
    import pygame
    pygame.display.init()
    w, h = pygame.display.get_desktop_sizes()[0]
    os.environ["PP_WINDOW"] = "%dx%d" % (max(800, min(1280, w - 80)), max(520, min(860, h - 150)))

import main  # noqa: E402

try:
    sys.exit(main.App().run())
except Exception:
    with open(os.path.join(home, "pocketpads_crash.log"), "w") as f:
        traceback.print_exc(file=f)
    sys.exit(1)
