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
requirements = python3,numpy,pygame,pyjnius,android
# MANAGE_EXTERNAL_STORAGE: to read the PocketPads folder on shared storage (the app sends you to the switch for it)
# Audio files only (no "all files access"). The Bluetooth search is declared as not being for location, so
# location is asked for only on Android 11 and older, where a search cannot be made without it.
android.permissions = READ_MEDIA_AUDIO,(name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32),(name=android.permission.BLUETOOTH_SCAN;usesPermissionFlags=neverForLocation),BLUETOOTH_CONNECT,(name=android.permission.BLUETOOTH;maxSdkVersion=30),(name=android.permission.BLUETOOTH_ADMIN;maxSdkVersion=30),(name=android.permission.ACCESS_FINE_LOCATION;maxSdkVersion=30),RECORD_AUDIO
# What `./build.sh release` makes: a bundle for the Play Store (see README.md for the signing key)
android.release_artifact = aab
# the Java half of MIDI in (see AndroidMidi in pocketpads/midi.py)
android.add_src = java
# icon.png is art/pocketpads.png at half size on a square of its own sky colour
icon.filename = %(source.dir)s/../android/icon.png
presplash.filename = %(source.dir)s/art/pocketpads.png
android.presplash_color = #111217
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
