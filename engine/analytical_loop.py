import hashlib
import os
import gc
import shutil
import uproot
import boost_histogram as bh

from ui.state import get_run_state, update_run_state
from engine.path_filter import (filter_raw_event_data, fill_histogram_from_cache,
                                  make_cache_acc, save_cache, filter_selection_cache)
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
    detector   = cfg["detector"]
    selections = cfg.get("selections")

    if not selections:
        # Fallback: build a single selection from flat cfg fields
        selections = [{
            "h5_sel":    cfg.get("h5_sel", cfg["h5"]),
            "sel_exprs": cfg.get("sel_exprs", []),
            "node_name": "",
            "histograms": cfg.get("histograms", [{
                "observable": cfg["observable"],
                "bins": cfg["bins"], "min": cfg["min"], "max": cfg["max"],
                "target": cfg["target"], "h5": cfg["h5"], "plot_idx": 0,
            }]),
        }]

    os.makedirs(os.path.join(hdir, "cache"),  exist_ok=True)
    os.makedirs(os.path.join(hdir, "output"), exist_ok=True)

    # Pre-compute the multiplicity component of the cache key (matches compile_graph_topology)
    mult_h5_base = cfg["energy"] + cfg["detector"] + str(cfg.get("mult_cuts", []))

    total_steps = len(selections) * len(active_samples)
    step = 0
    processed_any = False

    for sel_cfg in selections:
        h5_sel    = sel_cfg["h5_sel"]
        histograms = sel_cfg["histograms"]

        # Build a branch-specific cfg for filter_raw_event_data (passes sel_exprs)
        branch_cfg = dict(cfg)
        branch_cfg["sel_exprs"] = sel_cfg["sel_exprs"]
        branch_cfg["h5_sel"]    = h5_sel

        for idx, s in enumerate(active_samples):
            if get_run_state("stop"):
                update_run_state("running", False)
                return False

            update_run_state("progress", float(step) / max(1, total_steps))
            sel_cache = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")

            # ── Ensure selection cache exists ────────────────────────────────
            if not os.path.exists(sel_cache):
                derived = False
                sel_exprs_list = sel_cfg.get("sel_exprs", [])

                # If this selection is a chain prefix (>1 expression), check whether
                # the parent prefix cache already exists and filter it instead of
                # re-reading the ROOT file.
                if len(sel_exprs_list) > 1:
                    parent_exprs = sel_exprs_list[:-1]
                    parent_h5 = hashlib.md5(
                        (mult_h5_base + str(parent_exprs)).encode()
                    ).hexdigest()
                    parent_cache = os.path.join(hdir, "cache", f"sel_{parent_h5}_{s}.npz")
                    if os.path.exists(parent_cache):
                        update_run_state("status_msg",
                                         f"[{idx+1}/{len(active_samples)}] "
                                         f"filtering from parent cache")
                        try:
                            filter_selection_cache(
                                parent_cache, [sel_exprs_list[-1]], sel_cache
                            )
                            derived = True
                        except Exception as err:
                            update_run_state("status_msg",
                                             f"Cache filter error: {err}")

                if not derived:
                    data_file = os.path.join(os.getcwd(), "datasets", detector,
                                             cfg["energy"].replace(" ", ""), f"{s}.root")
                    if not os.path.exists(data_file):
                        data_file = os.path.join(hdir, "datasets", detector,
                                                 cfg["energy"].replace(" ", ""), f"{s}.root")
                        if not os.path.exists(data_file):
                            update_run_state("status_msg", f"Missing data: {s}")
                            step += 1
                            continue

                    update_run_state("status_msg",
                                     f"Processing [{idx+1}/{len(active_samples)}]")
                    cache_acc = make_cache_acc()

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
                                    arrays, nev, branch_cfg, None, None, None, "",
                                    idx, len(active_samples), entries_processed, total_entries,
                                    cache_acc=cache_acc,
                                )
                                if stop_req or get_run_state("stop"):
                                    update_run_state("running", False)
                                    return False
                                entries_processed += nev
                                del arrays
                                gc.collect()

                        save_cache(sel_cache, cache_acc)

                    except Exception as err:
                        update_run_state("status_msg", f"Error reading {s}: {err}")
                        step += 1
                        continue

            if not os.path.exists(sel_cache):
                step += 1
                continue

            processed_any = True

            # ── Fill each histogram in this branch from the selection cache ──
            for hcfg in histograms:
                if get_run_state("stop"):
                    update_run_state("running", False)
                    return False

                plot_idx   = hcfg.get("plot_idx", 0)
                hist_cache = os.path.join(hdir, "output", f"h5_{hcfg['h5']}_{s}.root")
                out_path   = os.path.join(hdir, "output", f"hist{plot_idx}_{s}.root")

                if os.path.exists(hist_cache):
                    shutil.copy(hist_cache, out_path)
                    update_run_state("status_msg",
                                     f"[{idx+1}/{len(active_samples)}] hist{plot_idx} (cache)")
                    continue

                outHist = hist()
                outHist.create(int(hcfg["bins"]), float(hcfg["min"]), float(hcfg["max"]))
                fill_histogram_from_cache(sel_cache, outHist, hcfg["observable"],
                                          idx, len(active_samples))
                write_final_histograms(hdir, s, hcfg["h5"], outHist, out_path)

            step += 1

    update_run_state("progress", 1.0)
    return processed_any
