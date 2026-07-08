import hashlib
import os
import shutil
import threading
import uproot
import boost_histogram as bh
from concurrent.futures import ThreadPoolExecutor, as_completed

from ui.state import (get_run_state, update_run_state,
                      add_active_node, add_completed_node, mark_nodes_completed)
from engine.path_filter import (filter_raw_event_data, fill_histogram_from_cache,
                                  make_cache_acc, save_cache, filter_selection_cache)
from engine.path_final import write_final_histograms

from paths import get_fce_home

hdir = get_fce_home()

# OPT-3: parallel workers per selection branch; each sample is independent
_MAX_WORKERS = 4


class hist:
    def __init__(self):
        self.h = {}

    def create(self, bins, min_val, max_val):
        ax = bh.axis.Regular(bins, min_val, max_val)
        self.h["h"] = bh.Histogram(ax)


def _process_sample(sel_cfg, s, idx, active_samples, mult_h5_base, cfg,
                    compiled_sel_exprs, step_counter, step_lock, total_steps):
    """Process one sample for one selection branch: build cache then fill histograms."""
    n_samp = len(active_samples)
    h5_sel = sel_cfg["h5_sel"]
    histograms = sel_cfg["histograms"]

    branch_cfg = dict(cfg)
    branch_cfg["sel_exprs"] = sel_cfg["sel_exprs"]
    branch_cfg["compiled_sel_exprs"] = compiled_sel_exprs  # OPT-2
    branch_cfg["h5_sel"] = h5_sel

    if get_run_state("stop"):
        return False

    sel_cache = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")

    # ── Ensure selection cache exists ────────────────────────────────────────
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
                                 f"[{idx+1}/{n_samp}] filtering from parent cache")
                try:
                    last_expr = sel_exprs_list[-1]
                    compiled_last = ([compile(last_expr, '<sel>', 'eval')]
                                     if last_expr else [])
                    filter_selection_cache(
                        parent_cache, [last_expr], sel_cache,
                        compiled_exprs=compiled_last,
                    )
                    derived = True
                except Exception as err:
                    update_run_state("status_msg", f"Cache filter error: {err}")

        if not derived:
            data_file = os.path.join(os.getcwd(), "datasets", cfg["detector"],
                                     cfg["energy"].replace(" ", ""), f"{s}.root")
            if not os.path.exists(data_file):
                data_file = os.path.join(hdir, "datasets", cfg["detector"],
                                         cfg["energy"].replace(" ", ""), f"{s}.root")
                if not os.path.exists(data_file):
                    update_run_state("status_msg", f"Missing data: {s}")
                    with step_lock:
                        step_counter[0] += 1
                        update_run_state(
                            "progress",
                            min(0.78, step_counter[0] / max(1, total_steps) * 0.80),
                        )
                    return False

            update_run_state("status_msg", f"Processing [{idx+1}/{n_samp}]")
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
                            idx, n_samp, entries_processed, total_entries,
                            cache_acc=cache_acc,
                        )
                        if stop_req or get_run_state("stop"):
                            update_run_state("running", False)
                            return False
                        entries_processed += nev
                        del arrays
                        # OPT-5: CPython's reference counting frees `arrays` on `del`;
                        # explicit gc.collect() between baskets is unnecessary overhead.

                save_cache(sel_cache, cache_acc)

            except Exception as err:
                update_run_state("status_msg", f"Error reading {s}: {err}")
                with step_lock:
                    step_counter[0] += 1
                    update_run_state(
                        "progress",
                        min(0.99, step_counter[0] / max(1, total_steps)),
                    )
                return False

    if not os.path.exists(sel_cache):
        with step_lock:
            step_counter[0] += 1
            update_run_state("progress",
                             min(0.99, step_counter[0] / max(1, total_steps)))
        return False

    # ── Fill each histogram in this branch from the selection cache ──────────
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
                             f"[{idx+1}/{n_samp}] hist{plot_idx} (cache)")
            continue

        outHist = hist()
        outHist.create(int(hcfg["bins"]), float(hcfg["min"]), float(hcfg["max"]))
        fill_histogram_from_cache(sel_cache, outHist, hcfg["observable"],
                                  idx, n_samp)
        write_final_histograms(hdir, s, hcfg["h5"], outHist, out_path)

    with step_lock:
        step_counter[0] += 1
        update_run_state("progress",
                         min(0.78, step_counter[0] / max(1, total_steps) * 0.80))
    return True


def run_physics_loop(cfg, samples, active_samples, en):
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
    step_counter = [0]   # mutable so worker closures can increment it
    step_lock    = threading.Lock()
    processed_any = False

    for sel_cfg in selections:
        sel_nid  = sel_cfg.get("nid")
        sel_name = sel_cfg.get("node_name", "")

        # OPT-2: compile selection expressions once per selection branch,
        # shared across all sample workers (code objects are read-only).
        compiled_sel_exprs = [
            compile(e, '<sel>', 'eval')
            for e in sel_cfg.get("sel_exprs", []) if e and e.strip()
        ]

        # Check if all selection-level caches already exist so we can
        # show the right visual state immediately (avoid false "active" flash).
        h5_sel = sel_cfg["h5_sel"]
        all_cached = all(
            os.path.exists(os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz"))
            for s in active_samples
        )

        if sel_nid is not None:
            if all_cached:
                add_completed_node(sel_nid)
            else:
                add_active_node(sel_nid)
                update_run_state("current_phase",
                                 f"Processing: {sel_name}" if sel_name else "Filtering events...")

        # OPT-3: process samples for this selection branch in parallel.
        # Each sample writes to a unique cache path — no cross-sample data races.
        n_workers = min(_MAX_WORKERS, len(active_samples))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(
                    _process_sample,
                    sel_cfg, s, idx, active_samples, mult_h5_base, cfg,
                    compiled_sel_exprs, step_counter, step_lock, total_steps,
                ): s
                for idx, s in enumerate(active_samples)
            }
            for fut in as_completed(futures):
                if get_run_state("stop"):
                    for f in futures:
                        f.cancel()
                    update_run_state("running", False)
                    return False
                try:
                    if fut.result():
                        processed_any = True
                except Exception as e:
                    update_run_state("status_msg", f"Sample error: {e}")

        # Mark this selection and all its observable/histogram nodes as done
        if sel_nid is not None:
            nids_to_complete = {sel_nid}
            for hcfg in sel_cfg.get("histograms", []):
                if hcfg.get("obs_nid") is not None:
                    nids_to_complete.add(hcfg["obs_nid"])
                if hcfg.get("hist_nid") is not None:
                    nids_to_complete.add(hcfg["hist_nid"])
            mark_nodes_completed(nids_to_complete)

    update_run_state("progress", 0.80)
    return processed_any
