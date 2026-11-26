# PocketPads for the iPad (experimental, in progress)

The same Python app as every other version, run inside an Xcode project. It gets its own screen
layout, worked by touch alone: see `pocketpads/tablet.py`.

## Build

Needs a Mac with Xcode and the GitHub CLI (`gh`).

```sh
./build.sh          # build for the iPad simulator
./build.sh run      # build, then install and start it on a simulator
./build.sh run "iPad Pro 13-inch (M5)"
./build.sh device   # build for a real iPad, sign it, install it and start it
```

`device` reads two private settings from `~/.config/pocketpads/ios.env`: `PP_IOS_TEAM` (your Apple
developer team id) and `PP_IOS_DEVICE` (the iPad's name or id, as `xcrun devicectl list devices` shows it).
The iPad has to be unlocked, trusted by the Mac, and have Developer Mode switched on.

The first run downloads an Xcode template from
[seekerluke/pygame-ios-templates](https://github.com/seekerluke/pygame-ios-templates) (MIT), which
holds Python 3.13, SDL2, numpy and pygame-ce 2.5.5 built for iOS. It is kept in `.build/` and is not
part of this repository. `build.sh` then changes the template once (name, landscape only, scene
support) and copies `pocketpads/` into it on every build. To start again from a clean template,
delete `.build/project`.

To watch it, open the Simulator app. The app's files, with `pocketpads.log`, are in its Documents
folder: `xcrun simctl get_app_container booted io.github.prone.pocketpads data`.

## To try the tablet layout without an iPad

```sh
PP_UI=tablet PP_WINDOW=1210x834 python3 pocketpads/main.py
```

The mouse stands in for a finger.

## How it is put together

- `scene.m` is added to the template's `main.m`. iOS 27 closes an app that does not use the scene
  lifecycle, and SDL2 predates it; this gives the app a scene and hands SDL's windows to it.
- `app_main.py` becomes the app's `__main__.py` and starts `main.py` as a script.
- The user's projects and samples are in the app's Documents folder, which shows in the Files app.

## Where it stands

- It has run on one iPad Pro 13-inch: pads, knobs and every page were tried there by hand.
- Written and tested on a Mac, where the same code runs, but not yet tried on an iPad: MIDI
  controllers (Core MIDI; BLUETOOTH MIDI on the PROJ page opens the system's list of Bluetooth
  controllers), recording from the microphone, and reading MP3, M4A, AAC, AIFF and FLAC files.
- Pad lights on the SMC-PAD.
- It is drawn at the screen's size in points and scaled up, which looks fine on the one iPad tried.
- An App Store or TestFlight build: for now it is installed from Xcode, on your own iPad.
