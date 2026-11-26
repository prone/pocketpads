"""pygame for the Android build: python-for-android's own recipe is pinned to pygame 2.1.0, which does not
build on current Python. This points the same recipe at pygame-ce (the maintained fork, same import name)."""
import glob
import os
import shutil
from os.path import basename, join

from pythonforandroid.logger import info
from pythonforandroid.recipes.pygame import Pygame2Recipe


class PygameCeRecipe(Pygame2Recipe):
    version = "2.5.5"
    url = "https://github.com/pygame-community/pygame-ce/archive/refs/tags/{version}.tar.gz"
    hostpython_prerequisites = ["setuptools", "cython"]     # its setup.py turns .pyx files into C

    def install_python_package(self, arch, name=None, env=None, is_dir=True):
        """Put the package together by hand. `pip install .` would read pygame-ce's pyproject.toml and start a
        second, meson build that cannot cross-compile here; build_ext has already made everything we need."""
        build = self.get_build_dir(arch.arch)
        dest = join(self.ctx.get_python_install_dir(arch.arch), "pygame")
        info("Installing pygame-ce into %s" % dest)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(join(build, "src_py"), dest,
                        ignore=shutil.ignore_patterns("__briefcase", "__pyinstaller", "meson.build", "*.icns", "*.ico"))
        built = glob.glob(join(build, "build", "lib.*", "pygame"))[0]
        for root, _dirs, files in os.walk(built):
            out = join(dest, os.path.relpath(root, built))
            os.makedirs(out, exist_ok=True)
            for f in files:
                if f.endswith(".so"):
                    # Built by the host's Python, so named for the host ("...-darwin.so"); Android's Python
                    # looks for its own tag or a bare ".so".
                    shutil.copy2(join(root, f), join(out, basename(f).split(".")[0] + ".so"))


recipe = PygameCeRecipe()
