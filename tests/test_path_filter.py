import os
import sys
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.path_filter import (
    _delta_r, _P4Proxy, _ArrayProxy, _delta_r_vec,
    make_cache_acc, save_cache, _CACHE_KEYS, _check_object_availability,
    _extract_min_count, check_obs_bounds_for_selection,
)


class _Obj:
    pass


def test_delta_r_zero_same_direction():
    a = _Obj()
    a.eta = 1.0
    a.phi = 0.5
    b = _Obj()
    b.eta = 1.0
    b.phi = 0.5
    assert _delta_r(a, b) == 0.0


def test_delta_r_known_value():
    a = _Obj()
    a.eta = 0.0
    a.phi = 0.0
    b = _Obj()
    b.eta = 1.0
    b.phi = 0.0
    assert abs(_delta_r(a, b) - 1.0) < 1e-10


def test_delta_r_phi_wrap():
    a = _Obj()
    a.eta = 0.0
    a.phi = math.pi - 0.1
    b = _Obj()
    b.eta = 0.0
    b.phi = -math.pi + 0.1
    dr = _delta_r(a, b)
    assert dr < 0.3


def test_p4proxy_mass():
    e = np.array([10.0])
    px = np.array([0.0])
    py = np.array([0.0])
    pz = np.array([0.0])
    p4 = _P4Proxy(e, px, py, pz)
    assert abs(float(p4.mass[0]) - 10.0) < 1e-8


def test_p4proxy_add():
    p4a = _P4Proxy(np.array([5.0]), np.array([3.0]), np.array([0.0]), np.array([0.0]))
    p4b = _P4Proxy(np.array([5.0]), np.array([3.0]), np.array([0.0]), np.array([0.0]))
    combined = p4a + p4b
    assert float(combined._e[0]) == 10.0
    assert float(combined._px[0]) == 6.0


def test_p4proxy_pt():
    p4 = _P4Proxy(np.array([10.0]), np.array([3.0]), np.array([4.0]), np.array([0.0]))
    assert abs(float(p4.pt[0]) - 5.0) < 1e-8


def test_array_proxy_missing_key_returns_sentinel():
    data = {"weight": np.array([1.0, 2.0])}
    proxy = _ArrayProxy("l1", data)
    vals = proxy.nonexistent
    assert all(v == -999.0 for v in vals)


def test_array_proxy_reads_key():
    data = {
        "weight": np.array([1.0, 2.0]),
        "l1_pt": np.array([30.0, 40.0]),
    }
    proxy = _ArrayProxy("l1", data)
    assert float(proxy.pt[0]) == 30.0
    assert float(proxy.pt[1]) == 40.0


def test_delta_r_vec_zero():
    a = _Obj()
    a.eta = np.array([1.0])
    a.phi = np.array([0.5])
    b = _Obj()
    b.eta = np.array([1.0])
    b.phi = np.array([0.5])
    dr = _delta_r_vec(a, b)
    assert abs(float(dr[0])) < 1e-10


def test_make_cache_acc_has_all_keys():
    acc = make_cache_acc()
    for key in _CACHE_KEYS:
        assert key in acc
        assert hasattr(acc[key], "__len__")  # pre-allocated numpy array
    assert acc["_n"] == 0


def test_check_object_availability_no_warning():
    data = {
        "weight": np.array([1.0, 1.0]),
        "nlep":   np.array([2, 3]),
        "njets":  np.array([2, 2]),
        "nphot":  np.array([0, 0]),
    }
    warnings = _check_object_availability(data, 2, "l2.pt > 10")
    assert warnings == []


def test_check_object_availability_warns_l2():
    data = {
        "weight": np.array([1.0, 1.0, 1.0]),
        "nlep":   np.array([1, 2, 1]),
        "njets":  np.array([0, 0, 0]),
        "nphot":  np.array([0, 0, 0]),
    }
    warnings = _check_object_availability(data, 3, "l2.pt > 10")
    assert len(warnings) == 1
    assert "l2" in warnings[0]
    assert "2" in warnings[0]


def test_check_object_availability_no_l2_reference():
    data = {
        "weight": np.array([1.0]),
        "nlep":   np.array([1]),
        "njets":  np.array([0]),
        "nphot":  np.array([0]),
    }
    warnings = _check_object_availability(data, 1, "l1.pt > 10")
    assert warnings == []


def test_extract_min_count_gte():
    assert _extract_min_count("nlep >= 2 and l1.pt > 10", "nlep") == 2


def test_extract_min_count_gt():
    assert _extract_min_count("nlep > 1", "nlep") == 2


def test_extract_min_count_eq():
    assert _extract_min_count("nlep == 3", "nlep") == 3


def test_extract_min_count_lhs_lte():
    assert _extract_min_count("2 <= nlep", "nlep") == 2


def test_extract_min_count_lhs_lt():
    assert _extract_min_count("1 < nlep", "nlep") == 2


def test_extract_min_count_no_constraint():
    assert _extract_min_count("l1.pt > 10", "nlep") == 0


def test_extract_min_count_jets():
    assert _extract_min_count("njets >= 2 and j1.btag > 0.7", "njets") == 2


def test_obs_bounds_no_second_objects():
    """No l2/j2/ph2 in observable → no warnings."""
    assert check_obs_bounds_for_selection("nlep >= 1", ["l1.pt"]) == []


def test_obs_bounds_l2_sufficient():
    """Selection guarantees nlep >= 2 → no warning for l2 in observable."""
    assert check_obs_bounds_for_selection("nlep >= 2", ["l2.pt"]) == []


def test_obs_bounds_l2_insufficient():
    """Selection only guarantees 1 lepton → warn when observable uses l2."""
    warns = check_obs_bounds_for_selection("nlep >= 1", ["l2.pt"])
    assert len(warns) == 1
    assert "l2.*" in warns[0]
    assert "nlep >= 2" in warns[0]


def test_obs_bounds_no_sel_constraint():
    """Selection with no count constraint → warn when observable uses l2."""
    warns = check_obs_bounds_for_selection("l1.pt > 10", ["l2.pt"])
    assert len(warns) == 1
    assert "l2.*" in warns[0]


def test_obs_bounds_vecsum_l2():
    """Vec Sum expression (l1.p4 + l2.p4).mass triggers warning."""
    warns = check_obs_bounds_for_selection("nlep >= 1", ["(l1.p4 + l2.p4).mass"])
    assert len(warns) == 1
    assert "l2.*" in warns[0]


def test_obs_bounds_j2_insufficient():
    warns = check_obs_bounds_for_selection("njets >= 1", ["j2.btag"])
    assert len(warns) == 1
    assert "j2.*" in warns[0]


def test_obs_bounds_multiple_warnings():
    warns = check_obs_bounds_for_selection("l1.pt > 5", ["l2.pt + j2.pt"])
    assert len(warns) == 2


def test_save_cache_and_reload(tmp_path):
    from engine.path_filter import _append_event, _P
    acc = make_cache_acc()
    null = _P()
    w_obj = _P(pt=0.0, eta=0.0, phi=0.0, e=0.0)
    _append_event(acc, 2, 1, 1, 0, 0, null, null, null, null, null, null, w_obj, 1.5)

    cache_file = str(tmp_path / "test_cache.npz")
    save_cache(cache_file, acc)
    data = np.load(cache_file)
    assert abs(float(data["weight"][0]) - 1.5) < 1e-5
    assert int(data["nlep"][0]) == 2
