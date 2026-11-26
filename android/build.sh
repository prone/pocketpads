#!/bin/sh
# Build the experimental Android app. Needs a JDK 17 on PATH or in JAVA_HOME, and the Android SDK named in buildozer.spec.
cd "$(dirname "$0")" || exit 1
export VIRTUAL_ENV="$PWD/.venv"                 # or Buildozer tries a --user install, which a venv refuses
export PATH="$VIRTUAL_ENV/bin:${JAVA_HOME:+$JAVA_HOME/bin:}/opt/homebrew/bin:$PATH"
# `./build.sh release` makes a signed bundle for the Play Store. It needs the signing key's four P4A_RELEASE_*
# settings (see README.md), read from the file named in PP_SIGNING or from ~/.config/pocketpads/signing.env.
SIGN=""
if [ "${1:-debug}" = "release" ]; then
  FILE="${PP_SIGNING:-$HOME/.config/pocketpads/signing.env}"
  [ -f "$FILE" ] || { echo "no signing settings at $FILE" >&2; exit 1; }
  SIGN=$(grep '^P4A_RELEASE_' "$FILE" | tr '\n' ' ')
  # `./build.sh release apk` makes a signed APK instead, for installing directly without the Play Store.
  [ "$2" = "apk" ] && SIGN="$SIGN APP_ANDROID_RELEASE_ARTIFACT=apk"
fi
# A clean environment: Buildozer prints every variable it was started with into its log. With a release that
# includes the key's password, so the password is blanked on the way out.
# shellcheck disable=SC2086
env -i HOME="$HOME" PATH="$PATH" VIRTUAL_ENV="$VIRTUAL_ENV" ${JAVA_HOME:+JAVA_HOME="$JAVA_HOME"} LANG=en_US.UTF-8 $SIGN \
  buildozer android "${1:-debug}" 2>&1 | sed -E 's/(PASSWD[^=]*=)[^ ,]*/\1***/g; s/(--(store|key)pass[= ])[^ ]*/\1***/g; s/(-P(KEYSTORE|KEYALIAS)_PASSWD=)[^ ]*/\1***/g'
