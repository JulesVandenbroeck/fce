import json
import os
import traceback

from ui.state import get_run_state, update_run_state
safe_get_state = get_run_state
safe_set_state = update_run_state

from engine.analytical_loop import run_physics_loop
from engine.downloader import run_dataset_download  # noqa: F401 (re-exported)
from engine.plotter import render_plots

from paths import get_fce_home

hdir = get_fce_home()


def execute_analysis(cfg, _unused):
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config", "samples.json")
        if not os.path.exists(config_path):
            config_path = os.path.join(hdir, "config", "samples.json")
        if not os.path.exists(config_path):
            safe_set_state("running", False)
            safe_set_state("status_msg", "Error: samples.json not found.")
            return

        with open(config_path) as f_json:
            samples = json.load(f_json)

        os.makedirs(os.path.join(hdir, "output"), exist_ok=True)
        os.makedirs(os.path.join(hdir, "cache"),  exist_ok=True)

        en = cfg["energy"].replace(" GeV", "")
        if en not in samples:
            safe_set_state("running", False)
            safe_set_state("status_msg", f"Error: energy '{en}' not in samples config.")
            return

        active_samples = list(samples[en].keys())

        safe_set_state("current_phase", "Reading events...")
        success = run_physics_loop(cfg, samples, active_samples, en)
        if not success or safe_get_state("stop"):
            safe_set_state("running", False)
            return

        if safe_get_state("stop"):
            safe_set_state("running", False)
            return

        safe_set_state("current_phase", "Rendering plots...")
        safe_set_state("progress", 0.85)
        try:
            render_plots(cfg, samples, en)
        except Exception as plot_err:
            safe_set_state("status_msg", f"Plot error: {plot_err}")

        # Statistical fit: find first histogram across all selections that has a target
        fit_candidates = []
        selections = cfg.get("selections")
        if selections:
            for sel_cfg in selections:
                for hcfg in sel_cfg["histograms"]:
                    fit_candidates.append(hcfg)
        else:
            fit_candidates = cfg.get("histograms", [{
                "observable": cfg["observable"],
                "bins": cfg["bins"], "min": cfg["min"], "max": cfg["max"],
                "target": cfg["target"], "h5": cfg["h5"], "plot_idx": 0,
            }])

        has_fit = any(
            hcfg.get("target", "None") not in ("None", None, "")
            for hcfg in fit_candidates
        )
        if has_fit:
            safe_set_state("current_phase", "Computing fit...")
            safe_set_state("progress", 0.95)

        for hcfg in fit_candidates:
            if hcfg.get("target", "None") in ("None", None, ""):
                continue
            try:
                from engine.fitter import run_fit
                fit_cfg = dict(cfg)
                fit_cfg.update(hcfg)
                mu, sig = run_fit(fit_cfg, samples, en,
                                  hist_idx=hcfg.get("plot_idx", 0))
                safe_set_state("fit_mu",  mu)
                safe_set_state("fit_sig", sig)
                break  # expose first successful fit result in the UI
            except Exception as fit_err:
                safe_set_state("status_msg", f"Fit error: {fit_err}")

        safe_set_state("current_phase", "")
        safe_set_state("progress", 1.0)
        safe_set_state("running",  False)

    except Exception as err:
        safe_set_state("running",    False)
        safe_set_state("status_msg", f"Engine error: {err}\n{traceback.format_exc()}")
