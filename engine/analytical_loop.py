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
                                  make_cache_acc, save_cache)
from engine.path_final import write_final_histograms

from paths import get_fce_home

hdir = get_fce_home()

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


def _process_sample(sel_cfg, s, idx, active_samples, cfg,
                    compiled_sel_exprs, progress_ctx):
    """Process one sample for one selection branch: build cache then fill histograms.

    progress_ctx keys:
      tasks_done     [int]   — selections saved so far (drives overall bar)
      tasks_total    [int]   — total (sel, sample) pairs to process
      samples_started [int]  — sequential counter: which worker number am I?
      plock          Lock    — guards tasks_done and samples_started
      n_workers      int     — how many parallel slots exist
      slot_pool      list    — available slot indices (claimed on ROOT open, freed on .npz save)
      slot_lock      Lock    — guards slot_pool and worker_data
      worker_data    dict    — slot -> {sample, done, total} for per-worker bar updates
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
    # Always read from ROOT with ALL combined expressions ([expr_A, expr_B, ...]).
    # Chained selections have the full expression list compiled into sel_exprs by
    # compile_graph_topology, so there is no need to derive from a parent cache.
    # This ensures every worker reads its own ROOT file independently and all N
    # workers run in true parallel for every selection branch.
    if not os.path.exists(sel_cache):
        slot = -1  # worker slot index; -1 = not yet claimed

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

                # Claim the lowest free slot so bars fill top-to-bottom.
                with progress_ctx["slot_lock"]:
                    if progress_ctx["slot_pool"]:
                        slot = progress_ctx["slot_pool"].pop(0)
                        progress_ctx["worker_data"][slot] = {
                            "sample": s, "done": 0, "total": num_entries,
                        }

                with progress_ctx["plock"]:
                    progress_ctx["samples_started"][0] += 1
                update_run_state("status_msg", "Processing...")

                for arrays in tr.iterate(v_keys, step_size="15 MB", library="np"):
                    if get_run_state("stop"):
                        update_run_state("running", False)
                        break
                    nev = len(arrays["weight"])
                    _, _, stop_req = filter_raw_event_data(
                        arrays, nev, branch_cfg, None, "",
                        cache_acc=cache_acc,
                    )
                    if stop_req or get_run_state("stop"):
                        update_run_state("running", False)
                        break

                    # Per-basket: update this worker's progress in the UI bar.
                    if slot >= 0:
                        with progress_ctx["slot_lock"]:
                            if slot in progress_ctx["worker_data"]:
                                progress_ctx["worker_data"][slot]["done"] += nev
                    del arrays
                    # OPT-5: CPython reference-counting frees arrays immediately;
                    # explicit gc.collect() between baskets is unnecessary overhead.

            if get_run_state("stop"):
                if slot >= 0:
                    with progress_ctx["slot_lock"]:
                        progress_ctx["slot_pool"].append(slot)
                        progress_ctx["slot_pool"].sort()
                        progress_ctx["worker_data"].pop(slot, None)
                update_run_state("running", False)
                return False

            save_cache(sel_cache, cache_acc)

        except Exception as err:
            update_run_state("status_msg", f"Error reading {s}: {err}")
            if slot >= 0:
                with progress_ctx["slot_lock"]:
                    progress_ctx["slot_pool"].append(slot)
                    progress_ctx["slot_pool"].sort()
                    progress_ctx["worker_data"].pop(slot, None)
            return False

        # Release slot after .npz is saved so queued samples can claim it.
        if slot >= 0:
            with progress_ctx["slot_lock"]:
                progress_ctx["slot_pool"].append(slot)
                progress_ctx["slot_pool"].sort()
                progress_ctx["worker_data"].pop(slot, None)

        # Advance overall bar by one completed (selection, sample) task.
        if os.path.exists(sel_cache):
            with progress_ctx["plock"]:
                progress_ctx["tasks_done"][0] += 1
                tot = progress_ctx["tasks_total"][0]
                if tot > 0:
                    update_run_state(
                        "progress",
                        min(0.78, progress_ctx["tasks_done"][0] / tot * 0.78),
                    )

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
        fill_histogram_from_cache(sel_cache, outHist, hcfg["observable"])
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

    # ── Number of parallel workers (user-configurable via UI spinner) ─────────
    n_workers = max(1, min(get_run_state("n_workers"), len(active_samples)))

    # ── Pre-compute event totals for worker bar denominators ─────────────────
    # Priority: download-time cache → quick ROOT header scan (no event iteration).
    # The denominator is fixed before any worker starts to prevent mid-run ratio drops.
    event_counts = _load_event_counts()
    ec_prefix = f"{cfg['detector']}_{cfg['energy'].replace(' ', '')}_"

    header_cache: dict[str, int] = {}   # s -> num_entries, avoids re-opening per selection
    for s in active_samples:
        cnt = event_counts.get(ec_prefix + s, 0)
        if cnt == 0:
            data_file = _find_data_file(cfg, s)
            if data_file:
                try:
                    with uproot.open(data_file) as f:
                        header_cache[s] = f["ntuple"].num_entries
                except Exception:
                    header_cache[s] = 0
        else:
            header_cache[s] = cnt

    # ── Task-completion progress: count (sel, sample) pairs ──────────────────
    # tasks_total = all pairs; tasks_pre_cached = pairs whose cache already exists.
    tasks_total = len(selections) * len(active_samples)
    tasks_pre_cached = sum(
        1
        for sel_cfg in selections
        for s in active_samples
        if os.path.exists(os.path.join(hdir, "cache", f"sel_{sel_cfg['h5_sel']}_{s}.npz"))
    )

    progress_ctx = {
        "tasks_done":      [tasks_pre_cached],
        "tasks_total":     [tasks_total],
        "samples_started": [0],
        "plock":           threading.Lock(),
        "n_workers":       n_workers,
        "slot_pool":       list(range(n_workers)),
        "slot_lock":       threading.Lock(),
        "worker_data":     {},   # slot -> {sample, done, total}
    }

    # Expose to main-thread poller via RUN_STATE so it can update DPG worker bars.
    update_run_state("progress_ctx", progress_ctx)

    # Set initial overall progress to reflect pre-cached work.
    if tasks_total > 0:
        update_run_state("progress", tasks_pre_cached / tasks_total * 0.78)

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
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(
                    _process_sample,
                    sel_cfg, s, idx, active_samples, cfg,
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
