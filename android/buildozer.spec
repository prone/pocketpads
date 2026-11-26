# Experimental Android build of PocketPads (python-for-android through Buildozer).
# From this folder: .venv/bin/buildozer android debug
[app]
title = PocketPads
package.name = pocketpads
package.domain = io.github.prone
source.dir = ../pocketpads
source.include_exts = py,png
source.exclude_dirs = tests,__pycache__,_vendor,art/__pycache__
source.exclude_patterns = state.json,*.log,PocketPads.sh
version = 0.5
requirements = python3,numpy,pygame
orientation = landscape
fullscreen = 1
android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True
p4a.bootstrap = sdl2
p4a.local_recipes = ./recipes

[buildozer]
log_level = 2
warn_on_root = 1
