# PocketPads for the Mac and for Windows (experimental)

The same Python app as every other version, packed into an ordinary app with
[PyInstaller](https://pyinstaller.org). It opens in a window with the touch layout
(`pocketpads/tablet.py`), so every control can be clicked or dragged with the mouse or trackpad.
The keyboard keys listed in the main README still work.

Your projects, samples and recordings are kept in `Music/PocketPads` in your home folder.

| | Mac | Windows |
|---|---|---|
| Sound | yes | yes |
| MIDI controllers in, notes and clock out | Core MIDI: USB, and Bluetooth ones connected in Audio MIDI Setup; plug in at any time | through pygame: the controllers present when the app starts |
| Recording from the microphone | yes | yes |
| MP3, M4A, AAC, AIFF, FLAC files | the system's own readers | MP3, OGG and FLAC; others need ffmpeg installed |
| Pad lights on the SMC-PAD | no | no |
| Tried on | one MacBook (Apple silicon) | GitHub's Windows machines only: the tests pass and the app starts there, but nobody has played it on a real PC yet |

## Build for the Mac

Needs Python 3.11 or newer.

```sh
./build.sh            # makes .build/dist/PocketPads.app, for this Mac
./build.sh release    # signs it with a Developer ID, has Apple notarize it, and zips it
```

`release` reads two private settings from `~/.config/pocketpads/mac.env`; the comment at the top of
`build.sh` says which. The app is built for the kind of Mac it is built on (Apple silicon or Intel).

## Build for Windows

`.github/workflows/windows.yml` runs the tests on Windows, builds the app, starts it once and keeps
the result as a download on the workflow's page. Start it from the Actions tab, or with
`gh workflow run windows.yml`. To build on your own PC, run the `pyinstaller` command from that file
inside `desktop/`.

## To run from the source instead

```sh
pip install -r requirements.txt
PP_UI=tablet PP_WINDOW=1280x800 python pocketpads/main.py
```

Leave out `PP_UI=tablet` for the handheld's screens, worked from the keyboard.
