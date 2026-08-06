import io
import os
import warnings
import contextlib
import numpy as np
import uproot

from paths import get_fce_home
from engine.systematics import LUMI_UNC, SYST_SOURCES

hdir = get_fce_home()

_SIG_CAP = 10.0  # cap reported significance to avoid inf for p0=0


def _counting_significance(n_tot: float, s_tot: float) -> float:
    """Background-free significance: sqrt(2*n) approximation (Asimov, b=0)."""
    return min(float(np.sqrt(2.0 * n_tot)) if n_tot > 0 else 0.0, _SIG_CAP)


def run_fit(cfg, samples, en, hist_idx=0):
    """Run pyhf signal fit. Returns (mu_best, significance) or (None, None)."""
    target = cfg.get("target", "None")
    if not target or target == "None":
        return None, None

    signal_vals = None
    bkg_vals = None
    data_obs = None

    # Per-source UP variation accumulators; keyed by source name.
    sig_up = {src: None for src in SYST_SOURCES}
    bkg_up = {src: None for src in SYST_SOURCES}
    # Track which sources have complete templates for both signal and background.
    src_ok = {src: True for src in SYST_SOURCES}

    for s in samples.get(en, {}).keys():
        root_out = os.path.join(hdir, "output", f"hist{hist_idx}_{s}.root")
        if not os.path.exists(root_out):
            continue
        try:
            with uproot.open(root_out) as f:
                if "h" not in f:
                    continue
                v = f["h"].values().tolist()

                # Read available variation templates for this sample.
                samp_up = {}
                for src in SYST_SOURCES:
                    key = f"h_{src}_up"
                    if key in f:
                        samp_up[src] = f[key].values().tolist()
                    else:
                        samp_up[src] = None

            if s == target:
                signal_vals = v
                for src in SYST_SOURCES:
                    if samp_up[src] is not None:
                        sig_up[src] = samp_up[src]
                    else:
                        src_ok[src] = False
            elif s == "data":
                data_obs = v
            else:
                bkg_vals = v if bkg_vals is None else [b + vi for b, vi in zip(bkg_vals, v)]
                for src in SYST_SOURCES:
                    if samp_up[src] is not None:
                        if bkg_up[src] is None:
                            bkg_up[src] = samp_up[src][:]
                        else:
                            bkg_up[src] = [a + b for a, b in zip(bkg_up[src], samp_up[src])]
                    else:
                        # Missing template for at least one bkg sample — drop this source.
                        src_ok[src] = False
        except Exception:
            continue

    if signal_vals is None:
        return None, None

    # Validate per-source availability: drop sources where bkg template is missing
    # (signal source was already checked above).
    for src in SYST_SOURCES:
        if bkg_up[src] is None and bkg_vals is not None:
            src_ok[src] = False

    # Background-free case: no other MC samples present
    if bkg_vals is None:
        if data_obs is None:
            data_obs = signal_vals[:]
        n_tot = float(np.sum(data_obs))
        s_tot = float(np.sum(signal_vals))
        if s_tot <= 0:
            return None, None
        mu_est = n_tot / s_tot
        sig = _counting_significance(n_tot, s_tot)
        return round(mu_est, 3), round(sig, 2)

    if data_obs is None:
        data_obs = [b + s for b, s in zip(bkg_vals, signal_vals)]

    # Drop bins where both signal and background are zero — they make the fit singular
    mask = [b > 0 or s > 0 for b, s in zip(bkg_vals, signal_vals)]
    if not any(mask):
        return None, None
    signal_nom = [s for s, m in zip(signal_vals, mask) if m]
    bkg_nom = [b for b, m in zip(bkg_vals, mask) if m]
    data_obs = [d for d, m in zip(data_obs, mask) if m]

    # Apply the same mask to variation arrays.
    for src in SYST_SOURCES:
        if src_ok[src]:
            if sig_up[src] is not None:
                sig_up[src] = [v for v, m in zip(sig_up[src], mask) if m]
            if bkg_up[src] is not None:
                bkg_up[src] = [v for v, m in zip(bkg_up[src], mask) if m]

    if sum(signal_nom) <= 0:
        return None, None

    bkg_unc = [max(float(np.sqrt(b)), 0.01) for b in bkg_nom]

    try:
        import pyhf

        # ── Signal modifiers ────────────────────────────────────────────────
        signal_modifiers = [{"name": "mu", "type": "normfactor", "data": None}]
        lumi_mod = {
            "name": "lumi", "type": "normsys",
            "data": {"hi": 1.0 + LUMI_UNC, "lo": max(0.01, 1.0 - LUMI_UNC)},
        }
        signal_modifiers.append(lumi_mod)

        for src in SYST_SOURCES:
            if src_ok[src] and sig_up[src] is not None:
                lo_data = [max(2.0 * n - u, 0.0)
                           for n, u in zip(signal_nom, sig_up[src])]
                signal_modifiers.append({
                    "name": src, "type": "histosys",
                    "data": {"hi_data": sig_up[src], "lo_data": lo_data},
                })

        # ── Background modifiers ────────────────────────────────────────────
        bkg_modifiers = [
            {"name": "bkg_unc", "type": "shapesys", "data": bkg_unc},
            lumi_mod,
        ]

        for src in SYST_SOURCES:
            if src_ok[src] and bkg_up[src] is not None:
                lo_data = [max(2.0 * n - u, 0.0)
                           for n, u in zip(bkg_nom, bkg_up[src])]
                bkg_modifiers.append({
                    "name": src, "type": "histosys",
                    "data": {"hi_data": bkg_up[src], "lo_data": lo_data},
                })

        spec = {
            "channels": [{"name": "singlechannel", "samples": [
                {"name": "signal", "data": signal_nom,
                 "modifiers": signal_modifiers},
                {"name": "background", "data": bkg_nom,
                 "modifiers": bkg_modifiers},
            ]}],
            "observations": [{"name": "singlechannel", "data": data_obs}],
            "measurements": [{"name": "Measurement",
                              "config": {"poi": "mu", "parameters": []}}],
            "version": "1.0.0",
        }
        model = pyhf.Model(spec)
        obs_data = pyhf.tensorlib.astensor(data_obs + model.config.auxdata)

        _sink = io.StringIO()
        with warnings.catch_warnings(), contextlib.redirect_stdout(_sink), \
                contextlib.redirect_stderr(_sink):
            warnings.simplefilter("ignore")
            fit_result = pyhf.infer.mle.fit(obs_data, model)
            mu_fit = float(pyhf.tensorlib.to_numpy(fit_result)[model.config.poi_index])

            # Discovery significance (q0 test)
            p0 = float(pyhf.infer.hypotest(0.0, obs_data, model, test_stat="q0"))

        from scipy.stats import norm as _norm
        if p0 <= 0.0:
            significance = _SIG_CAP          # p0=0 → beyond numerical range → cap
        elif p0 >= 1.0:
            significance = 0.0
        else:
            significance = min(float(_norm.isf(p0)), _SIG_CAP)

        return round(mu_fit, 3), round(significance, 2)

    except Exception:
        # Fallback: simple counting estimate
        s_sum = float(np.sum(signal_nom))
        b_sum = float(np.sum(bkg_nom))
        n_sum = float(np.sum(data_obs))
        mu_est = (n_sum - b_sum) / max(s_sum, 1e-6)
        if b_sum <= 0:
            sig_est = _counting_significance(n_sum, s_sum)
        else:
            sig_est = min(s_sum / np.sqrt(b_sum), _SIG_CAP)
        return round(mu_est, 3), round(float(sig_est), 2)
