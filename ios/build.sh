#!/bin/sh
# Builds PocketPads for the iPad (experimental).
#   ./build.sh            build for the iPad simulator
#   ./build.sh run [name] build, then install and start it on a simulator (default "iPad Pro 11-inch (M5)")
# Needs a Mac with Xcode and the GitHub CLI. The first run downloads the pygame-ce iOS template
# (seekerluke/pygame-ios-templates, MIT: Python 3.13, SDL2, numpy and pygame-ce 2.5.5 built for iOS).
set -e
cd "$(dirname "$0")"
B=.build; T=$B/project; ZIP=$B/pygame-ios-template-2.5.5.zip
mkdir -p $B
[ -f $ZIP ] || gh release download v0.0.3 -R seekerluke/pygame-ios-templates -p 'pygame-ios-template-2.5.5.zip' -D $B
if [ ! -d $T ]; then
  unzip -q $ZIP -d $T
  PBX=$T/pygame-ios.xcodeproj/project.pbxproj; PL=$T/pygame-ios/pygame-ios-Info.plist
  sed -i '' -e 's/IPHONEOS_DEPLOYMENT_TARGET = 13.0;/IPHONEOS_DEPLOYMENT_TARGET = 16.0;/g' \
            -e 's/PRODUCT_BUNDLE_IDENTIFIER = com.example.pygame-ios;/PRODUCT_BUNDLE_IDENTIFIER = io.github.prone.pocketpads;/g' \
            -e 's/PRODUCT_NAME = "$(TARGET_NAME)";/PRODUCT_NAME = PocketPads;/g' $PBX
  cat scene.m >> $T/pygame-ios/main.m
  pb() { /usr/libexec/PlistBuddy -c "$1" $PL; }
  S=":UIApplicationSceneManifest:UISceneConfigurations"
  pb "Delete $S"; pb "Add $S dict"; pb "Add $S:UIWindowSceneSessionRoleApplication array"
  pb "Add $S:UIWindowSceneSessionRoleApplication:0 dict"
  pb "Add $S:UIWindowSceneSessionRoleApplication:0:UISceneConfigurationName string Default"
  pb "Add $S:UIWindowSceneSessionRoleApplication:0:UISceneDelegateClassName string PPSceneDelegate"
  pb "Add :UIApplicationSupportsIndirectInputEvents bool true"
  pb "Set :MainModule pocketpads"
  pb "Set :CFBundleShortVersionString 0.5"
  pb "Add :UIStatusBarHidden bool true"
  pb "Add :UIFileSharingEnabled bool true"                 # the Documents folder shows in the Files app
  pb "Add :LSSupportsOpeningDocumentsInPlace bool true"
  pb "Add :NSMicrophoneUsageDescription string 'To record your own sounds onto the pads.'"
  pb "Add :NSBluetoothAlwaysUsageDescription string 'To connect Bluetooth MIDI controllers.'"
  for K in UISupportedInterfaceOrientations UISupportedInterfaceOrientations~ipad; do
    pb "Delete :$K"; pb "Add :$K array"
    pb "Add :$K:0 string UIInterfaceOrientationLandscapeLeft"; pb "Add :$K:1 string UIInterfaceOrientationLandscapeRight"
  done
  rm -rf "$T/pygame-ios/app/pygame-ios" "$T/pygame-ios/app/pygame-ios-0.0.1.dist-info"
fi
# The app itself: the same Python files as every other version.
A=$T/pygame-ios/app/pocketpads
rm -rf "$A"; mkdir -p "$A"
rsync -a --exclude '__pycache__' --exclude 'projects' --exclude 'samples' --exclude 'tests' --exclude '_vendor' \
      --exclude '*.log' --exclude 'state.json' --exclude 'buttons.json' ../pocketpads/ "$A/"
cp app_main.py "$A/__main__.py"
if [ "$1" = "device" ]; then
  # A real iPad. The Apple developer team and the iPad to use are private, so they are read from a file
  # outside the repository: PP_IOS_TEAM=<team id> and PP_IOS_DEVICE=<name or id, as `xcrun devicectl list devices` shows>.
  ENV="${PP_IOS_ENV:-$HOME/.config/pocketpads/ios.env}"
  [ -f "$ENV" ] || { echo "no $ENV: see the comment in build.sh"; exit 1; }
  . "$ENV"
  xcodebuild -project $T/pygame-ios.xcodeproj -scheme pygame-ios -configuration Debug -sdk iphoneos \
    -destination "generic/platform=iOS" -derivedDataPath $B/dd-device -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$PP_IOS_TEAM" CODE_SIGN_STYLE=Automatic build > $B/xcodebuild-device.log 2>&1 \
    || { grep "error:" $B/xcodebuild-device.log | sed "s/$PP_IOS_TEAM/<team>/g" | head; echo "build failed: see ios/$B/xcodebuild-device.log"; exit 1; }
  APP=$B/dd-device/Build/Products/Debug-iphoneos/PocketPads.app
  echo "built $APP"
  xcrun devicectl device install app --device "${2:-$PP_IOS_DEVICE}" "$APP" | tail -2
  xcrun devicectl device process launch --terminate-existing --device "${2:-$PP_IOS_DEVICE}" io.github.prone.pocketpads | tail -1
  exit 0
fi
xcodebuild -project $T/pygame-ios.xcodeproj -scheme pygame-ios -configuration Debug -sdk iphonesimulator \
  -destination "generic/platform=iOS Simulator" -derivedDataPath $B/dd build > $B/xcodebuild.log 2>&1 \
  || { grep "error:" $B/xcodebuild.log | head; echo "build failed: see ios/$B/xcodebuild.log"; exit 1; }
APP=$B/dd/Build/Products/Debug-iphonesimulator/PocketPads.app
echo "built $APP"
if [ "$1" = "run" ]; then
  SIM="${2:-iPad Pro 11-inch (M5)}"
  U=$(python3 pick_simulator.py "$SIM")              # the newest system that has a simulator of that name
  xcrun simctl boot "$U" 2>/dev/null || true
  xcrun simctl bootstatus "$U" > /dev/null
  xcrun simctl install "$U" "$APP"
  xcrun simctl launch --terminate-running-process "$U" io.github.prone.pocketpads
  echo "simulator $U"
fi
