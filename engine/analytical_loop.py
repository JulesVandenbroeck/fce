import os
import gc
import shutil
import numpy as np
import uproot
import boost_histogram as bh

from ui.state import get_run_state, update_run_state
from engine.path_filter import (filter_raw_event_data, fill_histogram_from_cache,
                                  make_cache_acc, save_cache)
from engine.path_final import write_final_histograms

from paths import get_fce_home

hdir = get_fce_home()


class hist:
    def __init__(self):
        self.h = {}

    def create(self, bins, min_val, max_val):
        ax = bh.axis.Regular(bins, min_val, max_val)
        self.h["h"] = bh.Histogram(ax)


def run_physics_loop(cfg, samples, active_samples, en):
    total_samples = len(active_samples)
    detector      = cfg["detector"]
    bins          = int(cfg["bins"])
    min_range     = float(cfg["min"])
    max_range     = float(cfg["max"])
    obs_target    = cfg["observable"]
    h5            = cfg["h5"]
    h5_sel        = cfg.get("h5_sel", h5)

    os.makedirs(os.path.join(hdir, "cache"),  exist_ok=True)
    os.makedirs(os.path.join(hdir, "output"), exist_ok=True)

    processed_any = False

    for idx, s in enumerate(active_samples):
        if get_run_state("stop"):
            update_run_state("running", False)
            return False

        update_run_state("progress", float(idx) / max(1, total_samples))

        outHist = hist()
        outHist.create(bins, min_range, max_range)

        # ── Level-2 cache: full-parameter histogram match ────────────────
        hist_cache = os.path.join(hdir, "output", f"h5_{h5}_{s}.root")
        if os.path.exists(hist_cache):
            update_run_state("status_msg", f"Processing [{idx+1}/{total_samples}] (histogram cache)")
            shutil.copy(hist_cache, os.path.join(hdir, "output", f"{s}.root"))
            processed_any = True
            continue

        # ── Level-1 cache: selection-passing events already saved ────────
        sel_cache = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")
        if os.path.exists(sel_cache):
            update_run_state("status_msg", f"Processing [{idx+1}/{total_samples}] (selection cache)")
            fill_histogram_from_cache(sel_cache, outHist, obs_target, idx, total_samples)
            write_final_histograms(hdir, s, cfg, outHist)
            processed_any = True
            continue

        # ── Full event loop ──────────────────────────────────────────────
        data_file = os.path.join(os.getcwd(), "datasets", detector,
                                 cfg["energy"].replace(" ", ""), f"{s}.root")
        if not os.path.exists(data_file):
            data_file = os.path.join(hdir, "datasets", detector,
                                     cfg["energy"].replace(" ", ""), f"{s}.root")
            if not os.path.exists(data_file):
                update_run_state("status_msg", f"Missing data: {s}")
                continue

        update_run_state("status_msg", f"Processing [{idx+1}/{total_samples}]")
        cache_acc = make_cache_acc()
        processed_any = True

        try:
            with uproot.open(data_file) as f_root:
                tr = f_root["ntuple"]
                total_entries = tr.num_entries
                v_keys = [k for k in tr.keys()
                          if "pt" in k or "eta" in k or "phi" in k
                          or "e" in k or "weight" in k or "btag" in k
                          or "d0signif" in k or "z0signif" in k]

                entries_processed = 0
                for arrays in tr.iterate(v_keys, step_size="15 MB", library="np"):
                    if get_run_state("stop"):
                        update_run_state("running", False)
                        return False

                    nev = len(arrays["weight"])
                    _, _, stop_req = filter_raw_event_data(
                        arrays, nev, cfg, outHist, None, None, obs_target,
                        idx, total_samples, entries_processed, total_entries,
                        cache_acc=cache_acc,
                    )
                    if stop_req or get_run_state("stop"):
                        update_run_state("running", False)
                        return False

                    entries_processed += nev
                    del arrays
                    gc.collect()

            # Save caches and histograms
            save_cache(sel_cache, cache_acc)
            write_final_histograms(hdir, s, cfg, outHist)

        except Exception as err:
            update_run_state("status_msg", f"Error reading {s}: {err}")

    update_run_state("progress", 1.0)
    return processed_any
