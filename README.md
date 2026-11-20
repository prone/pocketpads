# PocketPads

A 16-pad sample sequencer and sampler for the TrimUI Smart Pro handheld running KNULLI. Written in Python with pygame for the screen and controls and NumPy for the sound engine.

Version 0.03. Tested on one TrimUI Smart Pro running KNULLI Scarab.

## What it does

- Sixteen pads, a 16 or 32 step grid, four patterns, sixteen project slots
- Per-pad volume, pitch, filter, trim, fades, reverse, play mode, choke group, delay and reverb sends
- Note repeat, MPC-style swing, and hold-to-apply stutter, tape stop and filter sweeps
- Records from the built-in microphone and chops the take onto the pads
- Chop at hits, into equal pieces, or live; fit to tempo; spread one sound across the pads; resampling
- Loads WAV directly and MP3, FLAC, OGG and M4A through ffmpeg
- Imports recordings and slice points from GrainBrick if it is installed alongside

## Layout

- `pocketpads/` the app: `main.py` (screens and input), `engine.py` (sound), `kit.py` (built-in starter kit), `PocketPads.sh` (launcher), `art/` (menu and title artwork)
- `pocketpads/tests/` engine tests
- `site/` the manual, a single static page: open `site/index.html`

## Install on the device

Copy `pocketpads/` to `/userdata/roms/ports/pocketpads/` and `pocketpads/PocketPads.sh` to `/userdata/roms/ports/`. The app needs NumPy; the launcher looks for it in `pocketpads/_vendor/`.

## Run the tests

```
python pocketpads/tests/test_engine.py pocketpads
python pocketpads/tests/test_effects.py pocketpads
python pocketpads/tests/test_sampler.py pocketpads
```

## Run on a computer

```
PP_WINDOW=1280x720 python pocketpads/main.py
```

Keys: arrows, Z = A, X = B, S = X, A = Y, Q and W = L1 and R1, 1 and 2 = L2 and R2, Return = START, Tab = SELECT.
