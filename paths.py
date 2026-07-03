"""Resolves the writable FCE_STUDIO home directory (cache/output/datasets).

~/.fce is not always writable (read-only or quota-limited home directories
are common on shared clusters and containers), so this falls back to a
temp directory rather than crashing the app.
"""
import getpass
import os
import tempfile

_fce_home = None


def get_fce_home():
    """Return a writable FCE home directory, resolved and cached on first call."""
    global _fce_home
    if _fce_home is not None:
        return _fce_home

    candidates = []
    if os.environ.get("FCE_HOME"):
        candidates.append(os.environ["FCE_HOME"])
    candidates.append(os.path.join(os.path.expanduser("~"), ".fce"))
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    candidates.append(os.path.join(tempfile.gettempdir(), f"fce-{user}"))

    for candidate in candidates:
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".write_test")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
        except OSError:
            continue
        _fce_home = candidate
        return _fce_home

    # Should be unreachable — tempfile.gettempdir() is always writable in practice.
    raise OSError("No writable location found for FCE home directory.")


def configure_cache_env():
    """Redirect XDG_CACHE_HOME to a writable directory if needed.

    Some shared/managed Linux environments preset XDG_CACHE_HOME to a
    location the user can't write to (e.g. a container-wide /cache
    mount), which makes Mesa print an alarming "Permission denied"
    shader-cache warning on every launch. Point it at our own
    verified-writable home instead, unless the current value already
    works.
    """
    current = os.environ.get("XDG_CACHE_HOME")
    if current:
        try:
            os.makedirs(current, exist_ok=True)
            probe = os.path.join(current, ".write_test")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
            return
        except OSError:
            pass

    xdg_cache = os.path.join(get_fce_home(), "xdg_cache")
    os.makedirs(xdg_cache, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = xdg_cache
