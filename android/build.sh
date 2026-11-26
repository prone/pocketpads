#!/bin/sh
# Build the experimental Android app. Needs a JDK 17 on PATH or in JAVA_HOME, and the Android SDK named in buildozer.spec.
cd "$(dirname "$0")" || exit 1
export VIRTUAL_ENV="$PWD/.venv"                 # or Buildozer tries a --user install, which a venv refuses
export PATH="$VIRTUAL_ENV/bin:${JAVA_HOME:+$JAVA_HOME/bin:}/opt/homebrew/bin:$PATH"
# A clean environment: Buildozer prints every variable it was started with into its log.
exec env -i HOME="$HOME" PATH="$PATH" VIRTUAL_ENV="$VIRTUAL_ENV" ${JAVA_HOME:+JAVA_HOME="$JAVA_HOME"} LANG=en_US.UTF-8 \
  buildozer android "${1:-debug}"
