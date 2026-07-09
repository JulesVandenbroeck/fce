import io
import os
import warnings
import contextlib
import numpy as np
import uproot

from paths import get_fce_home

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
    bkg_vals    = None
    data_obs    = None

    for s in samples.get(en, {}).keys():
        root_out = os.path.join(hdir, "output", f"hist{hist_idx}_{s}.root")
        if not os.path.exists(root_out):
            continue
        try:
            with uproot.open(root_out) as f:
                if "h" not in f:
                    continue
                v = f["h"].values().tolist()
            if s == target:
                signal_vals = v
            elif s == "data":
                data_obs = v
            else:
                bkg_vals = v if bkg_vals is None else [b + vi for b, vi in zip(bkg_vals, v)]
        except Exception:
            continue

    if signal_vals is None:
        return None, None

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
    signal_vals = [s for s, m in zip(signal_vals, mask) if m]
    bkg_vals    = [b for b, m in zip(bkg_vals,    mask) if m]
    data_obs    = [d for d, m in zip(data_obs,    mask) if m]

    if sum(signal_vals) <= 0:
        return None, None

    bkg_unc = [max(float(np.sqrt(b)), 0.01) for b in bkg_vals]

    try:
        import pyhf
        model = pyhf.simplemodels.uncorrelated_background(
            signal=signal_vals,
            bkg=bkg_vals,
            bkg_uncertainty=bkg_unc,
        )
        obs_data = pyhf.tensorlib.astensor(data_obs + model.config.auxdata)

        _sink = io.StringIO()
        with warnings.catch_warnings(), contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
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
        s_sum = float(np.sum(signal_vals))
        b_sum = float(np.sum(bkg_vals))
        n_sum = float(np.sum(data_obs))
        mu_est = (n_sum - b_sum) / max(s_sum, 1e-6)
        if b_sum <= 0:
            sig_est = _counting_significance(n_sum, s_sum)
        else:
            sig_est = min(s_sum / np.sqrt(b_sum), _SIG_CAP)
        return round(mu_est, 3), round(float(sig_est), 2)
