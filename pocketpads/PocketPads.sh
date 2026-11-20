#!/bin/sh
# PocketPads: sample sequencer
# The app folder sits next to this script (on KNULLI: /userdata/roms/ports/pocketpads).
PORTS="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$PORTS/pocketpads"
if [ ! -f "$APPDIR/main.py" ]; then
  echo "PocketPads not installed: $APPDIR/main.py" >&2
  exit 1
fi
cd "$APPDIR" || exit 1
while :; do
  PYTHONPATH="$APPDIR/_vendor" python3 "$APPDIR/main.py" > "$APPDIR/launch.log" 2>&1
  rc=$?
  # Exit code 42 means "open GrainBrick": run it, then come straight back here when it quits.
  [ "$rc" = 42 ] || exit "$rc"
  GB=$(ls -t "$PORTS"/GrainBrick*.sh 2>/dev/null | head -1)
  [ -n "$GB" ] && sh "$GB"
  cd "$APPDIR" || exit 1
done
