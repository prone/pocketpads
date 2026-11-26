# Copied into the iPad app as pocketpads/__main__.py by build.sh: starts main.py the way it is started
# everywhere else, as a script with its own folder first on the path.
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
runpy.run_path(os.path.join(here, "main.py"), run_name="__main__")
