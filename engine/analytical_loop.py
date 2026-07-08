import hashlib
import json
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

# Persistent event-count cache stored at the FCE home root (not inside cache/,
# which is wiped on every startup) so counts survive across sessions.
_EVENT_COUNTS_FILE = os.path.join(hdir, "event_counts.json")


def _load_event_counts() -> dict:
    if os.path.exists(_EVENT_COUNTS_FILE):
        try:
            with open(_EVENT_COUNTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _find_data_file(cfg: dict, s: str) -> str | None:
    """Return the path to the ROOT file for sample *s*, or None if not found."""
    for base in (os.getcwd(), hdir):
        path = os.path.join(base, "datasets", cfg["detector"],
                            cfg["energy"].replace(" ", ""), f"{s}.root")
        if os.path.exists(path):
            return path
    return None


class hist:
    def __init__(self):
        self.h = {}

    def create(self, bins, min_val, max_val):
        ax = bh.axis.Regular(bins, min_val, max_val)
        self.h["h"] = bh.Histogram(ax)


def _process_sample(sel_cfg, s, idx, active_samples, mult_h5_base, cfg,
                    compiled_sel_exprs, progress_ctx):
    """Process one sample for one selection branch: build cache then fill histograms.

    progress_ctx keys:
      events_done    [int]  — running total events processed across all workers
      total_events   [int]  — pre-computed denominator (fixed before processing starts)
      samples_started [int] — sequential counter of samples that began ROOT reading
      plock          Lock   — guards events_done and samples_started
    """
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
            data_file = _find_data_file(cfg, s)
            if data_file is None:
                update_run_state("status_msg", f"Missing data: {s}")
                return False

            cache_acc = make_cache_acc()

            try:
                with uproot.open(data_file) as f_root:
                    tr = f_root["ntuple"]
                    num_entries = tr.num_entries
                    v_keys = [k for k in tr.keys()
                              if "pt" in k or "eta" in k or "phi" in k
                              or "e" in k or "weight" in k or "btag" in k
                              or "d0signif" in k or "z0signif" in k]

                    # Sequential counter: increments as each worker starts ROOT
                    # reading, so the UI always shows 1/N, 2/N, ... regardless
                    # of which sample index is processed first.
                    with progress_ctx["plock"]:
                        progress_ctx["samples_started"][0] += 1
                        sample_num = progress_ctx["samples_started"][0]
                    update_run_state("status_msg",
                                     f"Processing [{sample_num}/{n_samp}]")

                    entries_processed = 0
                    for arrays in tr.iterate(v_keys, step_size="15 MB", library="np"):
                        if get_run_state("stop"):
                            update_run_state("running", False)
                            return False
                        nev = len(arrays["weight"])
                        _, _, stop_req = filter_raw_event_data(
                            arrays, nev, branch_cfg, None, None, None, "",
                            idx, n_samp, entries_processed, num_entries,
                            cache_acc=cache_acc,
                        )
                        if stop_req or get_run_state("stop"):
                            update_run_state("running", False)
                            return False
                        entries_processed += nev

                        # Advance progress bar based on events processed
                        with progress_ctx["plock"]:
                            progress_ctx["events_done"][0] += nev
                            tot = progress_ctx["total_events"][0]
                            if tot > 0:
                                update_run_state(
                                    "progress",
                                    min(0.78, progress_ctx["events_done"][0] / tot * 0.80),
                                )
                        del arrays
                        # OPT-5: CPython's reference counting frees `arrays` on `del`;
                        # explicit gc.collect() between baskets is unnecessary overhead.

                save_cache(sel_cache, cache_acc)

            except Exception as err:
                update_run_state("status_msg", f"Error reading {s}: {err}")
                return False

    if not os.path.exists(sel_cache):
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

    # ── Pre-compute total events for progress denominator ────────────────────
    # For each non-cached (selection, sample) pair we need to know num_entries
    # BEFORE processing starts so the denominator never changes mid-run (which
    # would cause the bar to go backwards when the ratio drops).
    #
    # Priority:
    #   1. event_counts.json populated by the downloader (zero I/O).
    #   2. Quick ROOT header scan — uproot reads TTree metadata in microseconds,
    #      without iterating any events.  Result is NOT saved to disk; that is
    #      the downloader's job.
    event_counts = _load_event_counts()
    ec_prefix = f"{cfg['detector']}_{cfg['energy'].replace(' ', '')}_"

    total_events_init = 0
    header_cache: dict[str, int] = {}   # s -> num_entries, avoids re-opening per selection
    for sel_cfg in selections:
        h5_sel = sel_cfg["h5_sel"]
        for s in active_samples:
            sel_cache = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")
            if not os.path.exists(sel_cache):
                cnt = event_counts.get(ec_prefix + s, 0)
                if cnt == 0 and s not in header_cache:
                    # Fall back to ROOT header scan (fast, no event iteration)
                    data_file = _find_data_file(cfg, s)
                    if data_file:
                        try:
                            with uproot.open(data_file) as f:
                                header_cache[s] = f["ntuple"].num_entries
                        except Exception:
                            header_cache[s] = 0
                if cnt == 0:
                    cnt = header_cache.get(s, 0)
                total_events_init += cnt

    progress_ctx = {
        "events_done":     [0],
        "total_events":    [total_events_init],
        "samples_started": [0],
        "plock":           threading.Lock(),
    }
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
                    compiled_sel_exprs, progress_ctx,
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
