"""Past versions of a schemes repo's .itermcolors files, from the clone's git history.

setup.sh imports presets from the clone, then later pulls it. An installed preset equal to
an older version of its file was imported by setup.sh and not edited since, so it's safe
to update (srcp_import_presets.py) or remove (srcp_uninstall_prefs.py). A shallow clone
still has every version pulled since it was made, which covers everything it imported.
"""

import os
import plistlib
import subprocess
from xml.parsers.expat import ExpatError


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, check=True).stdout


def repo_root(schemes_dir):
    """The git checkout holding schemes_dir, or None if it isn't one."""
    try:
        return _git(schemes_dir, "rev-parse", "--show-toplevel").decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def past_versions(root, path):
    """Every committed version of path (a .itermcolors file), newest first, as dicts."""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    try:
        commits = _git(root, "log", "--format=%H", "--", rel).decode().split()
    except subprocess.CalledProcessError:
        return []
    versions = []
    for commit in commits:
        try:
            versions.append(plistlib.loads(_git(root, "show", f"{commit}:{rel}")))
        except (subprocess.CalledProcessError, plistlib.InvalidFileException, ExpatError):
            continue  # deleted in that commit, or not a plist then
    return versions


def from_repo(installed, path, root):
    """True if installed equals path's current contents or any committed version of it."""
    with open(path, "rb") as f:
        if installed == plistlib.load(f):
            return True
    return root is not None and any(installed == v for v in past_versions(root, path))
