# Contributing

Bug reports, device reports and patches are welcome.

## Reporting a problem

Please include the device and system (for example "TrimUI Smart Pro, KNULLI Scarab"), what you did, and what happened. After a session, `pocketpads/pocketpads.log` holds the button presses and a last line with the number of audio dropouts; `pocketpads_crash.log` holds the error if the app stopped. Both help a lot.

## Making a change

1. Run the tests before and after: `for t in engine effects sampler robustness styles midi; do python pocketpads/tests/test_$t.py pocketpads; done`
2. If you change the engine, add a check to the tests for it.
3. Keep the audio thread cheap. `Engine.render` runs every 11.6 ms on a slow ARM core: no per-sample Python loops, and no file access or large allocations there. The header of `engine.py` explains which thread may touch what.
4. Screen changes can be checked without a device by running with `PP_WINDOW=1280x720` and, for repeatable checks, a `PP_SCRIPT` file of button presses that saves screenshots.
5. Text a player has to read should be at least the `h` font size; the screen is small.

## Scope

PocketPads aims to stay a simple, immediate pad sampler. Features that add a screen or a mode are worth discussing in an issue first.

## Samples and third-party code

Do not add sample files, or code copied from other projects, unless their licence clearly allows it. The starter kit is synthesized for this reason.
