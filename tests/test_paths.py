import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paths import get_fce_home, configure_cache_env


def test_get_fce_home_returns_writable_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FCE_HOME", str(tmp_path))
    import paths
    paths._fce_home = None
    result = get_fce_home()
    assert os.path.isdir(result)
    probe = os.path.join(result, "probe.txt")
    with open(probe, "w") as f:
        f.write("ok")
    os.remove(probe)
    paths._fce_home = None


def test_get_fce_home_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("FCE_HOME", str(tmp_path))
    import paths
    paths._fce_home = None
    first = get_fce_home()
    second = get_fce_home()
    assert first == second
    paths._fce_home = None


def test_configure_cache_env_sets_writable_path(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("FCE_HOME", str(tmp_path))
    import paths
    paths._fce_home = None
    configure_cache_env()
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    assert xdg != ""
    assert os.path.isdir(xdg)
    paths._fce_home = None
