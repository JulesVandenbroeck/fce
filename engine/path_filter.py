import math
import numpy as np
import vector
from ui.state import get_run_state

_SAFE_BUILTINS = {
    "abs": abs, "max": max, "min": min, "len": len,
    "float": float, "int": int, "bool": bool,
    "sqrt": math.sqrt, "cos": math.cos, "sin": math.sin,
    "tan": math.tan, "pi": math.pi, "exp": math.exp, "log": math.log,
    "True": True, "False": False, "None": None,
}


class _P:
    """Physics object supporting attribute access; unset attr → -999."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _):
        return -999.0


def _delta_r(a, b):
    deta = a.eta - b.eta
    dphi = a.phi - b.phi
    while dphi >  math.pi: dphi -= 2 * math.pi
    while dphi < -math.pi: dphi += 2 * math.pi
    return math.sqrt(deta * deta + dphi * dphi)


# ---------------------------------------------------------------------------
# Vectorized proxies — evaluate an observable against all cached events at once
# ---------------------------------------------------------------------------

class _P4Proxy:
    """Numpy-backed 4-vector supporting vectorized arithmetic and .mass/.pt/.eta/.phi."""

    def __init__(self, e, px, py, pz):
        self._e = e; self._px = px; self._py = py; self._pz = pz

    def __add__(self, other):
        return _P4Proxy(self._e + other._e, self._px + other._px,
                        self._py + other._py, self._pz + other._pz)

    @property
    def mass(self):
        m2 = self._e**2 - self._px**2 - self._py**2 - self._pz**2
        return np.sqrt(np.maximum(m2, 0.0))

    @property
    def pt(self):
        return np.sqrt(self._px**2 + self._py**2)

    @property
    def phi(self):
        return np.arctan2(self._py, self._px)

    @property
    def eta(self):
        p  = np.sqrt(self._px**2 + self._py**2 + self._pz**2)
        ct = np.where(p > 1e-10, self._pz / p, 0.0)
        ct = np.clip(ct, -1.0 + 1e-10, 1.0 - 1e-10)
        return -0.5 * np.log((1.0 - ct) / (1.0 + ct))

    def deltaR(self, other):
        deta = self.eta - other.eta
        dphi = np.arctan2(np.sin(self.phi - other.phi), np.cos(self.phi - other.phi))
        return np.sqrt(deta**2 + dphi**2)


class _ArrayProxy:
    """Physics object backed by numpy arrays — one entry per cached event."""

    def __init__(self, prefix, data):
        self.__dict__.update({"_prefix": prefix, "_data": data, "_p4": None})

    def __getattr__(self, name):
        key = f"{self._prefix}_{name}"
        if key in self._data:
            return self._data[key].astype(np.float64)
        return np.full(len(self._data["weight"]), -999.0, dtype=np.float64)

    @property
    def p4(self):
        if self._p4 is None:
            d, pfx = self._data, self._prefix
            pt  = d[f"{pfx}_pt"].astype(np.float64)
            eta = d[f"{pfx}_eta"].astype(np.float64)
            phi = d[f"{pfx}_phi"].astype(np.float64)
            e   = d[f"{pfx}_e"].astype(np.float64)
            self.__dict__["_p4"] = _P4Proxy(
                e, pt * np.cos(phi), pt * np.sin(phi), pt * np.sinh(eta)
            )
        return self._p4


def _delta_r_vec(a, b):
    deta = a.eta - b.eta
    dphi = np.arctan2(np.sin(a.phi - b.phi), np.cos(a.phi - b.phi))
    return np.sqrt(deta**2 + dphi**2)


def _make_lepton(lep: dict) -> _P:
    p4 = vector.obj(pt=lep["pt"], eta=lep["eta"], phi=lep["phi"], e=lep["e"])
    return _P(pt=lep["pt"], eta=lep["eta"], phi=lep["phi"], e=lep["e"],
              d0=lep.get("d0", 0.0), z0=lep.get("z0", 0.0), p4=p4)


def _make_jet(j: dict) -> _P:
    p4 = vector.obj(pt=j["pt"], eta=j["eta"], phi=j["phi"], e=j["e"])
    return _P(pt=j["pt"], eta=j["eta"], phi=j["phi"], e=j["e"], btag=j["btag"], p4=p4)


def _make_photon(ph: dict) -> _P:
    p4 = vector.obj(pt=ph["pt"], eta=ph["eta"], phi=ph["phi"], e=ph["e"])
    return _P(pt=ph["pt"], eta=ph["eta"], phi=ph["phi"], e=ph["e"], p4=p4)


def _make_met(pt, eta, phi, e) -> _P:
    p4 = vector.obj(pt=pt, eta=eta, phi=phi, e=e)
    return _P(pt=pt, eta=eta, phi=phi, e=e, p4=p4)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_CACHE_KEYS = [
    "nlep", "nel", "nmu", "njets", "nphot", "weight",
    "l1_pt", "l1_eta", "l1_phi", "l1_e", "l1_d0", "l1_z0",
    "l2_pt", "l2_eta", "l2_phi", "l2_e", "l2_d0", "l2_z0",
    "j1_pt", "j1_eta", "j1_phi", "j1_e", "j1_btag",
    "j2_pt", "j2_eta", "j2_phi", "j2_e", "j2_btag",
    "ph1_pt", "ph1_eta", "ph1_phi", "ph1_e",
    "ph2_pt", "ph2_eta", "ph2_phi", "ph2_e",
    "met_pt", "met_eta", "met_phi", "met_e",
]

_INIT_CAP = 4096


def make_cache_acc() -> dict:
    # OPT-4: pre-allocated numpy arrays with exponential growth instead of Python lists.
    # Eliminates ~1 GB of CPython float-object overhead per million passing events.
    return {"_n": 0, "_cap": _INIT_CAP,
            **{k: np.empty(_INIT_CAP, dtype=np.float32) for k in _CACHE_KEYS}}


def _grow_acc(acc: dict):
    old_cap = acc["_cap"]
    new_cap = old_cap * 2
    for k in _CACHE_KEYS:
        new_arr = np.empty(new_cap, dtype=np.float32)
        new_arr[:old_cap] = acc[k]
        acc[k] = new_arr
    acc["_cap"] = new_cap


def _append_event(acc, nlep, nel, nmu, njets, nphot, l1, l2, j1, j2, ph1, ph2, met, w):
    i = acc["_n"]
    if i == acc["_cap"]:
        _grow_acc(acc)
    acc["nlep"][i] = nlep;  acc["nel"][i] = nel
    acc["nmu"][i] = nmu;    acc["njets"][i] = njets
    acc["nphot"][i] = nphot; acc["weight"][i] = w
    acc["l1_pt"][i] = l1.pt;   acc["l1_eta"][i] = l1.eta
    acc["l1_phi"][i] = l1.phi; acc["l1_e"][i] = l1.e
    acc["l1_d0"][i] = l1.d0;   acc["l1_z0"][i] = l1.z0
    acc["l2_pt"][i] = l2.pt;   acc["l2_eta"][i] = l2.eta
    acc["l2_phi"][i] = l2.phi; acc["l2_e"][i] = l2.e
    acc["l2_d0"][i] = l2.d0;   acc["l2_z0"][i] = l2.z0
    acc["j1_pt"][i] = j1.pt;   acc["j1_eta"][i] = j1.eta
    acc["j1_phi"][i] = j1.phi; acc["j1_e"][i] = j1.e
    acc["j1_btag"][i] = j1.btag
    acc["j2_pt"][i] = j2.pt;   acc["j2_eta"][i] = j2.eta
    acc["j2_phi"][i] = j2.phi; acc["j2_e"][i] = j2.e
    acc["j2_btag"][i] = j2.btag
    acc["ph1_pt"][i] = ph1.pt;  acc["ph1_eta"][i] = ph1.eta
    acc["ph1_phi"][i] = ph1.phi; acc["ph1_e"][i] = ph1.e
    acc["ph2_pt"][i] = ph2.pt;  acc["ph2_eta"][i] = ph2.eta
    acc["ph2_phi"][i] = ph2.phi; acc["ph2_e"][i] = ph2.e
    acc["met_pt"][i] = met.pt;  acc["met_eta"][i] = met.eta
    acc["met_phi"][i] = met.phi; acc["met_e"][i] = met.e
    acc["_n"] = i + 1


def save_cache(cache_file: str, acc: dict):
    n = acc["_n"]
    np.savez_compressed(cache_file, **{k: acc[k][:n] for k in _CACHE_KEYS})


def filter_selection_cache(parent_cache_path: str, additional_exprs: list,
                           output_cache_path: str, compiled_exprs=None):
    """Build a child selection cache by applying additional expressions to a parent cache.

    Used when the parent prefix cache already exists on disk (e.g. sel_[hash_A]_s.npz)
    so we only need to apply the new expression rather than re-reading the ROOT file.
    compiled_exprs: optional list of pre-compiled code objects matching additional_exprs.
    """
    data = np.load(parent_cache_path, mmap_mode='r')
    n = len(data["weight"])

    # ── Vectorized fast path ─────────────────────────────────────────────────
    # Evaluate the additional expression as a numpy boolean mask over all events.
    # numpy operations release the Python GIL, enabling true parallel execution
    # when multiple workers call this function on different samples simultaneously.
    # Produces the same output as the per-event fallback but orders of magnitude faster.
    exprs_to_eval = compiled_exprs if compiled_exprs else additional_exprs
    try:
        nphot_arr = data["nphot"] if "nphot" in data else np.zeros(n, dtype=np.float32)
        vec_vars = {
            "nlep": data["nlep"], "nel": data["nel"],
            "nmu":  data["nmu"],  "njets": data["njets"],
            "nphot": nphot_arr,
            "l1": _ArrayProxy("l1", data), "l2": _ArrayProxy("l2", data),
            "j1": _ArrayProxy("j1", data), "j2": _ArrayProxy("j2", data),
            "ph1": _ArrayProxy("ph1", data), "ph2": _ArrayProxy("ph2", data),
            "met": _ArrayProxy("met", data),
            "deltaR": _delta_r_vec,
        }
        mask = np.ones(n, dtype=bool)
        for expr in exprs_to_eval:
            if not expr:
                continue
            result = eval(expr, {"__builtins__": _SAFE_BUILTINS}, vec_vars)
            result = np.asarray(result, dtype=bool).ravel()
            if result.shape[0] != n:
                raise ValueError("shape mismatch")
            mask &= result
        # Save filtered arrays directly — same format as save_cache (float32 npz).
        np.savez_compressed(output_cache_path,
                            **{k: data[k][mask] for k in data.files})
        return
    except Exception:
        pass

    # ── Per-event fallback (handles 4-vector expressions the vectorized path can't) ─
    acc = make_cache_acc()
    _NULL = _P()

    for i in range(n):
        try:
            l1  = _obj_from_cache(data, i, "l1",  ["eta", "phi", "e", "d0", "z0"])
            l2  = _obj_from_cache(data, i, "l2",  ["eta", "phi", "e", "d0", "z0"])
            j1  = _obj_from_cache(data, i, "j1",  ["eta", "phi", "e", "btag"])
            j2  = _obj_from_cache(data, i, "j2",  ["eta", "phi", "e", "btag"])
            ph1 = (_obj_from_cache(data, i, "ph1", ["eta", "phi", "e"])
                   if "ph1_pt" in data else _NULL)
            ph2 = (_obj_from_cache(data, i, "ph2", ["eta", "phi", "e"])
                   if "ph2_pt" in data else _NULL)
            met_pt = float(data["met_pt"][i])
            met_p4 = vector.obj(pt=met_pt, eta=float(data["met_eta"][i]),
                                phi=float(data["met_phi"][i]), e=float(data["met_e"][i]))
            met    = _P(pt=met_pt, eta=float(data["met_eta"][i]),
                        phi=float(data["met_phi"][i]), e=float(data["met_e"][i]), p4=met_p4)
            local_vars = {
                "nlep": int(data["nlep"][i]), "nel": int(data["nel"][i]),
                "nmu":  int(data["nmu"][i]),  "njets": int(data["njets"][i]),
                "nphot": int(data["nphot"][i]) if "nphot" in data else 0,
                "l1": l1, "l2": l2, "j1": j1, "j2": j2,
                "ph1": ph1, "ph2": ph2, "met": met,
                "deltaR": _delta_r,
            }
            skip = False
            for expr in exprs_to_eval:
                if not expr:
                    continue
                try:
                    if not eval(expr, {"__builtins__": _SAFE_BUILTINS}, local_vars):
                        skip = True
                        break
                except Exception:
                    skip = True
                    break
            if skip:
                continue
            nel  = int(data["nel"][i])
            nmu  = int(data["nmu"][i])
            _append_event(acc,
                          nel + nmu, nel, nmu,
                          int(data["njets"][i]),
                          int(data["nphot"][i]) if "nphot" in data else 0,
                          l1, l2, j1, j2, ph1, ph2, met, float(data["weight"][i]))
        except Exception:
            continue

    save_cache(output_cache_path, acc)


def _obj_from_cache(data, i, prefix, keys, extra=None) -> _P:
    """Reconstruct a _P physics object from a loaded .npz cache."""
    pt = float(data[f"{prefix}_pt"][i])
    if pt <= -900:
        return _P()
    kw = {k: float(data[f"{prefix}_{k}"][i]) for k in keys}
    kw["pt"] = pt
    try:
        kw["p4"] = vector.obj(pt=pt, eta=kw["eta"], phi=kw["phi"], e=kw["e"])
    except Exception:
        pass
    return _P(**kw)


def fill_histogram_from_cache(cache_file: str, outHist, observable_target: str):
    """Load a selection-level cache and fill the histogram with a fresh observable eval."""
    # OPT-1: mmap_mode='r' lets the OS page in only accessed columns; unaccessed arrays
    # are never faulted into RAM (particularly useful in the vectorized path below).
    data = np.load(cache_file, mmap_mode='r')
    n = len(data["weight"])
    weights = data["weight"].astype(np.float64)

    # ── Vectorized fast path: evaluate observable over all events at once ──
    try:
        vec_vars = {
            "nlep": data["nlep"], "nel": data["nel"],
            "nmu":  data["nmu"],  "njets": data["njets"],
            "nphot": data["nphot"] if "nphot" in data else np.zeros(n, dtype=np.float32),
            "l1": _ArrayProxy("l1", data), "l2": _ArrayProxy("l2", data),
            "j1": _ArrayProxy("j1", data), "j2": _ArrayProxy("j2", data),
            "ph1": _ArrayProxy("ph1", data), "ph2": _ArrayProxy("ph2", data),
            "met": _ArrayProxy("met", data),
            "deltaR": _delta_r_vec,
        }
        vals = eval(observable_target, {"__builtins__": _SAFE_BUILTINS}, vec_vars)
        vals = np.asarray(vals, dtype=np.float64).ravel()
        if vals.shape[0] == n:
            mask = np.isfinite(vals) & (vals > -900.0)
            outHist.h["h"].fill(vals[mask], weight=weights[mask])
            return
    except Exception:
        pass

    # ── Per-event fallback (handles any expression the vectorized path can't) ─
    # OPT-2: pre-compile the observable expression once outside the event loop
    try:
        observable_code = compile(observable_target, '<obs>', 'eval')
    except Exception:
        observable_code = None

    _step = max(1, n // 100)
    for i in range(n):
        if i % _step == 0:
            if get_run_state("stop"):
                return
        try:
            l1 = _obj_from_cache(data, i, "l1", ["eta", "phi", "e", "d0", "z0"])
            l2 = _obj_from_cache(data, i, "l2", ["eta", "phi", "e", "d0", "z0"])
            j1 = _obj_from_cache(data, i, "j1", ["eta", "phi", "e", "btag"])
            j2 = _obj_from_cache(data, i, "j2", ["eta", "phi", "e", "btag"])
            ph1 = _obj_from_cache(data, i, "ph1", ["eta", "phi", "e"]) if "ph1_pt" in data else _P()
            ph2 = _obj_from_cache(data, i, "ph2", ["eta", "phi", "e"]) if "ph2_pt" in data else _P()
            met_pt = float(data["met_pt"][i])
            met_p4 = vector.obj(pt=met_pt, eta=float(data["met_eta"][i]),
                                phi=float(data["met_phi"][i]), e=float(data["met_e"][i]))
            met = _P(pt=met_pt, eta=float(data["met_eta"][i]),
                     phi=float(data["met_phi"][i]), e=float(data["met_e"][i]), p4=met_p4)
            local_vars = {
                "nlep": int(data["nlep"][i]), "nel": int(data["nel"][i]),
                "nmu":  int(data["nmu"][i]),  "njets": int(data["njets"][i]),
                "nphot": int(data["nphot"][i]) if "nphot" in data else 0,
                "l1": l1, "l2": l2, "j1": j1, "j2": j2,
                "ph1": ph1, "ph2": ph2, "met": met,
                "deltaR": _delta_r,
            }
            expr_or_code = observable_code if observable_code is not None else observable_target
            obs_val = eval(expr_or_code, {"__builtins__": _SAFE_BUILTINS}, local_vars)
            if obs_val is None:
                continue
            obs_val = float(obs_val)
            if obs_val <= -900:
                continue
            outHist.h["h"].fill(obs_val, weight=float(data["weight"][i]))
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Main per-basket filter
# ---------------------------------------------------------------------------

def filter_raw_event_data(arrays, nev, cfg, outHist, observable_target,
                          cache_acc=None):
    if get_run_state("stop"):
        return [], [], True

    has_el = "electron_pt" in arrays and len(arrays["electron_pt"]) > 0
    has_mu = "muon_pt"     in arrays and len(arrays["muon_pt"])     > 0
    has_jt = "jet_pt"      in arrays and len(arrays["jet_pt"])      > 0
    has_ph = "photon_pt"   in arrays and len(arrays["photon_pt"])   > 0

    w_arr       = arrays["weight"]
    met_pt_arr  = arrays["MET_pt"]
    met_phi_arr = arrays.get("MET_phi")
    met_eta_arr = arrays.get("MET_eta")
    met_e_arr   = arrays.get("MET_e")

    el_pt  = arrays["electron_pt"]  if has_el else None
    el_eta = arrays["electron_eta"] if has_el else None
    el_phi = arrays["electron_phi"] if has_el else None
    el_e   = arrays["electron_e"]   if has_el else None
    el_d0  = arrays.get("electron_d0signif") if has_el else None
    el_z0  = arrays.get("electron_z0signif") if has_el else None

    mu_pt  = arrays["muon_pt"]  if has_mu else None
    mu_eta = arrays["muon_eta"] if has_mu else None
    mu_phi = arrays["muon_phi"] if has_mu else None
    mu_e   = arrays["muon_e"]   if has_mu else None
    mu_d0  = arrays.get("muon_d0signif") if has_mu else None
    mu_z0  = arrays.get("muon_z0signif") if has_mu else None

    jet_pt   = arrays["jet_pt"]  if has_jt else None
    jet_eta  = arrays["jet_eta"] if has_jt else None
    jet_phi  = arrays["jet_phi"] if has_jt else None
    jet_e    = arrays["jet_e"]   if has_jt else None
    jet_btag = arrays.get("jet_btag") if has_jt else None

    ph_pt  = arrays["photon_pt"]  if has_ph else None
    ph_eta = arrays["photon_eta"] if has_ph else None
    ph_phi = arrays["photon_phi"] if has_ph else None
    ph_e   = arrays["photon_e"]   if has_ph else None

    mult_cuts = cfg.get("mult_cuts", [])
    sel_exprs = cfg.get("sel_exprs", [])
    # OPT-2: use pre-compiled expression objects when passed via cfg
    compiled_sel_exprs = cfg.get("compiled_sel_exprs", None)

    _NULL = _P()

    for i in range(nev):
        try:
            w       = float(w_arr[i])
            met_pt  = float(met_pt_arr[i])
            met_phi = float(met_phi_arr[i]) if met_phi_arr is not None else 0.0
            met_eta = float(met_eta_arr[i]) if met_eta_arr is not None else 0.0
            met_e   = float(met_e_arr[i])   if met_e_arr   is not None else met_pt

            nel   = len(el_pt[i]) if has_el else 0
            nmu   = len(mu_pt[i]) if has_mu else 0
            nlep  = nel + nmu
            njets = len(jet_pt[i]) if has_jt else 0
            nphot = len(ph_pt[i])  if has_ph else 0

            # ── Multiplicity cuts ────────────────────────────────────────
            skip = False
            for cut in mult_cuts:
                cut_nlep, cut_njets = cut[0], cut[1]
                ltype = cut[2] if len(cut) > 2 else "Any"
                cut_nphot = cut[3] if len(cut) > 3 else 0
                count = {"Electron": nel, "Muon": nmu}.get(ltype, nlep)
                if count < cut_nlep or njets < cut_njets or nphot < cut_nphot:
                    skip = True; break
            if skip:
                continue

            # ── Build lepton list (pt-sorted) ────────────────────────────
            leptons = []
            if has_el:
                for il in range(nel):
                    leptons.append({
                        "pt": float(el_pt[i][il]), "eta": float(el_eta[i][il]),
                        "phi": float(el_phi[i][il]), "e": float(el_e[i][il]),
                        "d0": float(el_d0[i][il]) if el_d0 is not None else 0.0,
                        "z0": float(el_z0[i][il]) if el_z0 is not None else 0.0,
                    })
            if has_mu:
                for im in range(nmu):
                    leptons.append({
                        "pt": float(mu_pt[i][im]), "eta": float(mu_eta[i][im]),
                        "phi": float(mu_phi[i][im]), "e": float(mu_e[i][im]),
                        "d0": float(mu_d0[i][im]) if mu_d0 is not None else 0.0,
                        "z0": float(mu_z0[i][im]) if mu_z0 is not None else 0.0,
                    })
            leptons.sort(key=lambda x: x["pt"], reverse=True)

            # ── Build jet list ───────────────────────────────────────────
            jets = []
            if has_jt:
                for ij in range(njets):
                    btag = float(jet_btag[i][ij]) if jet_btag is not None else 0.0
                    jets.append({"pt": float(jet_pt[i][ij]), "eta": float(jet_eta[i][ij]),
                                 "phi": float(jet_phi[i][ij]), "e":  float(jet_e[i][ij]),
                                 "btag": btag})

            # ── Build photon list (pt-sorted) ─────────────────────────────
            photons = []
            if has_ph:
                for ip in range(nphot):
                    photons.append({"pt": float(ph_pt[i][ip]), "eta": float(ph_eta[i][ip]),
                                    "phi": float(ph_phi[i][ip]), "e":  float(ph_e[i][ip])})
                photons.sort(key=lambda x: x["pt"], reverse=True)

            # ── Physics objects ──────────────────────────────────────────
            l1  = _make_lepton(leptons[0])  if len(leptons) >= 1 else _NULL
            l2  = _make_lepton(leptons[1])  if len(leptons) >= 2 else _NULL
            j1  = _make_jet(jets[0])        if len(jets)    >= 1 else _NULL
            j2  = _make_jet(jets[1])        if len(jets)    >= 2 else _NULL
            ph1 = _make_photon(photons[0])  if len(photons) >= 1 else _NULL
            ph2 = _make_photon(photons[1])  if len(photons) >= 2 else _NULL
            met = _make_met(met_pt, met_eta, met_phi, met_e)

            local_vars = {
                "nlep": nlep, "nel": nel, "nmu": nmu, "njets": njets, "nphot": nphot,
                "l1": l1, "l2": l2, "j1": j1, "j2": j2,
                "ph1": ph1, "ph2": ph2, "met": met,
                "deltaR": _delta_r,
            }

            # ── Selection expressions ────────────────────────────────────
            skip = False
            if compiled_sel_exprs is not None:
                for code in compiled_sel_exprs:
                    try:
                        if not eval(code, {"__builtins__": _SAFE_BUILTINS}, local_vars):
                            skip = True; break
                    except Exception:
                        skip = True; break
            else:
                for expr in sel_exprs:
                    if not expr:
                        continue
                    try:
                        if not eval(expr, {"__builtins__": _SAFE_BUILTINS}, local_vars):
                            skip = True; break
                    except Exception:
                        skip = True; break
            if skip:
                continue

            # ── Event passes all cuts — accumulate for cache ─────────────
            if cache_acc is not None:
                _append_event(cache_acc, nlep, nel, nmu, njets, nphot, l1, l2, j1, j2, ph1, ph2, met, w)

            # ── Observable evaluation ────────────────────────────────────
            if outHist is not None and observable_target:
                try:
                    obs_val = eval(observable_target, {"__builtins__": _SAFE_BUILTINS}, local_vars)
                    if obs_val is None:
                        continue
                    obs_val = float(obs_val)
                    if obs_val <= -900:
                        continue
                    outHist.h["h"].fill(obs_val, weight=w)
                except Exception:
                    continue

        except Exception:
            continue

    return [], [], False
