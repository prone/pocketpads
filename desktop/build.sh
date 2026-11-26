#!/bin/sh
# Builds PocketPads.app for the Mac (experimental) with PyInstaller.
#   ./build.sh             build desktop/.build/dist/PocketPads.app, signed for this Mac only
#   ./build.sh release     sign it with a Developer ID, have Apple notarize it, and zip it for download
# `release` reads two private settings from ~/.config/pocketpads/mac.env:
#   PP_MAC_IDENTITY="Developer ID Application: ..."   (as `security find-identity -v -p codesigning` shows it)
#   PP_MAC_NOTARY=<a profile saved with `xcrun notarytool store-credentials`>
# The Windows app is built from the same app_main.py by .github/workflows/windows.yml.
set -e
cd "$(dirname "$0")"
B=.build; V=$(sed -n 's/^version = //p' ../android/buildozer.spec)
PY="${PP_PYTHON:-python3}"                      # needs Python 3.11 or newer: PP_PYTHON=/path/to/python ./build.sh
[ -d $B/venv ] || { "$PY" -m venv $B/venv; $B/venv/bin/pip -q install -r ../requirements.txt pyinstaller; }
rm -rf $B/dist $B/work
$B/venv/bin/pyinstaller --noconfirm --log-level WARN --windowed --name PocketPads --paths ../pocketpads \
  --add-data "$PWD/../pocketpads/art:art" --exclude-module tests --osx-bundle-identifier io.github.prone.pocketpads \
  --distpath $B/dist --workpath $B/work --specpath $B app_main.py
APP=$B/dist/PocketPads.app; PL=$APP/Contents/Info.plist
pb() { /usr/libexec/PlistBuddy -c "$1" $PL; }
pb "Set :CFBundleShortVersionString $V"
pb "Add :NSMicrophoneUsageDescription string 'To record your own sounds onto the pads.'" || true
pb "Add :NSBluetoothAlwaysUsageDescription string 'To connect Bluetooth MIDI controllers.'" || true
pb "Add :LSMinimumSystemVersion string 12.0" || true
pb "Add :LSApplicationCategoryType string public.app-category.music" || true
if [ "$1" = "release" ]; then
  ENV="${PP_MAC_ENV:-$HOME/.config/pocketpads/mac.env}"
  [ -f "$ENV" ] || { echo "no $ENV: see the comment in build.sh"; exit 1; }
  . "$ENV"
  codesign --force --deep --options runtime --timestamp --entitlements entitlements.plist -s "$PP_MAC_IDENTITY" $APP 2>&1 | grep -v "replacing existing signature" || true
  Z=$B/PocketPads-$V-mac.zip
  rm -f $Z; ditto -c -k --keepParent $APP $Z
  xcrun notarytool submit $Z --keychain-profile "$PP_MAC_NOTARY" --wait | grep -E "status:|id:" | tail -2
  xcrun stapler staple -q $APP
  rm -f $Z; ditto -c -k --keepParent $APP $Z
  spctl -a -vv $APP 2>&1 | head -2 | sed 's/(.*)/(team hidden)/'
  echo "made $Z"
else
  codesign --force --deep --entitlements entitlements.plist -s - $APP 2>&1 | grep -v "replacing existing signature" || true
  echo "built $APP"
fi
