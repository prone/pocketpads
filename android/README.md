# Android build (experimental)

PocketPads packaged as an Android app with [python-for-android](https://github.com/kivy/python-for-android) through Buildozer. Tried on one device, a Retroid Pocket 4 Pro (Android 13): the screens, the buttons, both sticks, the built-in kit and sound work.

Your own sounds go in a `PocketPads` folder on the device's shared storage (WAV files; the app asks for "all files access" the first time the folder is opened). That part is new and not yet confirmed on a device.

MIDI in works over Bluetooth (an M-Vave SMC-PAD: pads confirmed) and should over USB, which is untried. A controller paired in Android's Bluetooth settings is picked up at launch; an unpaired one is found by BLUETOOTH MIDI on the PROJ page. The Java half is in `java/`.

Also working on a Retroid Pocket 4 Pro: the SMC-PAD's pad lights, recording from the microphone, and loading MP3 and other compressed files (through Android's own decoders, in `java/.../PPAudio.java`; AIFF is not among them). MIDI out and clock are built but untried.

Projects, samples and settings are kept in `files/pocketpads-data`, outside the program's folder, because an update empties that one.

## Build

Needs a JDK 17, plus `autoconf automake libtool pkg-config cmake` (Homebrew on a Mac). The first build downloads the Android SDK and NDK into `~/.buildozer` and takes a while.

```
python3 -m venv .venv
.venv/bin/pip install buildozer "cython<3.1"
JAVA_HOME=/path/to/jdk-17 ./build.sh
adb install -r bin/pocketpads-*-debug.apk
```

`recipes/pygame` swaps python-for-android's pygame 2.1.0, which no longer builds, for pygame-ce.
