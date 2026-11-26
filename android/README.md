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

## Touch

Every control works by touch. On a device with no buttons of its own, the first touch brings up on-screen buttons at the sides of the screen (d-pad, A B X Y, L1 L2 R1 R2, SELECT, START); they do exactly what physical buttons do, and can be held together. Pressing a physical button takes them away again. With or without them, pads, page tabs, pattern letters, steps, list rows, the name keyboard and dialogs answer a finger directly: a tap chooses a row, a second tap on it is A (its left and right thirds turn a value down and up). Android's back gesture is B.

## Your own samples

Put WAV, MP3 or other audio files in `Music/PocketPads` on the device's storage (the app makes the folder). The first time you open it in the sound browser, Android asks to allow "music and audio". The app asks for nothing wider: no "all files access", and no location on Android 12 and up.

## A release for the Play Store

`./build.sh release` makes a signed bundle, `bin/pocketpads-<version>-arm64-v8a-release.aab`. It reads the signing key's settings from `~/.config/pocketpads/signing.env` (or the file named in `PP_SIGNING`): four lines, `P4A_RELEASE_KEYSTORE`, `P4A_RELEASE_KEYALIAS`, `P4A_RELEASE_KEYSTORE_PASSWD` and `P4A_RELEASE_KEYALIAS_PASSWD`. Make a key with `keytool -genkeypair -keystore upload-key.jks -alias pocketpads-upload -keyalg RSA -keysize 4096 -validity 10000`. Keep the key and its password out of the repository. Still to do before a Play Store upload: target Android 16 (`android.api`), and raise `android.numeric_version` for every upload.
