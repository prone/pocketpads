"""Prints the id of the simulator with the given name on the newest iOS that has one (used by build.sh)."""
import json
import subprocess
import sys

name = sys.argv[1]
devices = json.loads(subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                                    capture_output=True, text=True, check=True).stdout)["devices"]
found = []
for runtime, sims in devices.items():
    if "iOS-" not in runtime:
        continue
    version = [int(n) for n in runtime.split("iOS-")[1].split("-")[:2] if n.isdigit()]
    found += [(version, s["udid"]) for s in sims if s["name"] == name]
if not found:
    sys.exit("no simulator named %r" % name)
print(max(found)[1])
