# Optional sounds

Extra sounds for PocketPads. None of this is needed to run the app, and the normal install does not copy it:
take the folders you want and leave the rest.

## Installing

Copy any folder from here into `pocketpads/samples/` on the device. On a TrimUI Smart Pro running KNULLI that is
`/userdata/roms/ports/pocketpads/samples/`. For example, to have everything:

```
scp -r packs/generated packs/cc0-sonic-pi root@<device address>:/userdata/roms/ports/pocketpads/samples/
```

In PocketPads the folders then appear in the sample browser (SOUND page, SAMPLE row, A):

- **A** on a file puts it on the selected pad, **X** plays it first.
- **Y** on a folder puts its first sixteen sounds on the sixteen pads of the bank on screen, so a kit loads in one go.

To remove a pack, delete its folder from `samples/`. Projects that used its sounds will then report them missing.

## What is here

| Folder | Sounds | Size | What it is | Licence |
|---|---|---|---|---|
| `generated/analog-drums` | 16 | 0.6 MB | Round, boomy drum-machine sounds | MIT, like the app |
| `generated/hard-drums` | 16 | 0.7 MB | Punchy, bright drums for techno and house | MIT |
| `generated/lofi-drums` | 14 | 1.0 MB | Drums through an old sampler, plus vinyl crackle and tape hiss | MIT |
| `generated/bass` | 10 | 0.9 MB | Ten bass sounds, all tuned to C | MIT |
| `generated/keys-and-chords` | 14 | 4.5 MB | Notes at middle C, and C minor and C major chords and pads | MIT |
| `generated/effects` | 12 | 2.5 MB | Risers, drops, hits and noises | MIT |
| `cc0-sonic-pi/` | 204 | 33 MB | Recorded drums, hats, electronic hits, percussion, tabla, bass, loops, ambient sounds, effects and guitar | CC0 (public domain) |

### generated

Made for PocketPads by `make_generated.py`, from arithmetic alone: no recordings are involved. They are part of this
project and under its MIT licence. Bass and key sounds are tuned to C, so the pad's PITCH setting and the step notes on
the BEAT page read as real notes.

To change them, edit `make_generated.py` and run `python packs/make_generated.py` (needs NumPy).

### cc0-sonic-pi

The sample set that ships with [Sonic Pi](https://sonic-pi.net), copied from `etc/samples` of
[sonic-pi-net/sonic-pi](https://github.com/sonic-pi-net/sonic-pi) at commit `52eedbb` and sorted into folders by kind.
The files themselves are unchanged FLAC files.

Sonic Pi's licence says of these samples: "All the bundled samples are individually licensed under a CC0 1.0 Universal
Public Domain Dedication. They have been obtained from freesound.org", with the samples named `arovane_` donated by Uwe
Zahn (Arovane) and those named `tbd_` by The Black Dog, also under CC0. [`cc0-sonic-pi/SOURCES.md`](cc0-sonic-pi/SOURCES.md)
is Sonic Pi's own list, unchanged, linking each sample to where it came from. CC0 asks for no credit; it is given here anyway.

Two files of the original set are left out, `loop_amen` and `loop_amen_full`: they are a drum break from a commercial
record, and whatever the uploader's label says, that is not something this project can vouch for.

PocketPads is not affiliated with Sonic Pi.

FLAC files are converted to WAV the first time each is used (the screen says so and pauses for a moment); after that they
load at once. The converted copies are kept in `samples/converted/`.
