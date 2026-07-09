import os
import time
import threading
import dearpygui.dearpygui as dpg
from ui.graph import (compile_graph_topology, check_pipeline_connectivity,
                      mark_nodes_from_pipeline_check, validate_node_expressions,
                      clear_all_node_errors, apply_node_runtime_states,
                      _clear_node_runtime_theme, _set_node_done)
from ui.state import get_run_state, update_run_state
from paths import get_fce_home

safe_get_state = get_run_state
safe_set_state = update_run_state

FCE_DIR = get_fce_home()
CURRENT_WORKER = None

MAX_HIST_TEXTURES = 8

# User-provided names for discovered processes: {plot_idx: str}
_NAMED_PROCESSES: dict[int, str] = {}
# Processes already discovered (no popup again this session): {plot_idx}
_DISCOVERED_PIDS: set[int] = set()
# Queue of (pidx, res) waiting to show discovery popups sequentially
_DISCOVERY_QUEUE: list = []
# Which plot_idx the currently open discovery popup refers to
_CURRENT_DISCOVERY_PIDX: list[int | None] = [None]


def _discovery_selection_label(res: dict) -> str:
    """Return a human-readable selection label for the discovery popup."""
    custom = res.get("sel_custom_name", "").strip()
    if custom:
        return custom
    exprs = res.get("sel_exprs", [])
    if exprs:
        return " AND ".join(exprs)
    return ""


def _show_next_discovery() -> None:
    if not _DISCOVERY_QUEUE or not dpg.does_item_exist("discovery_window"):
        if dpg.does_item_exist("discovery_window"):
            dpg.configure_item("discovery_window", show=False)
        return
    pidx, res = _DISCOVERY_QUEUE.pop(0)
    _CURRENT_DISCOVERY_PIDX[0] = pidx

    x_label  = res.get("x_label", "").strip() or f"Histogram {pidx + 1}"
    sel_label = _discovery_selection_label(res)

    detail_lines = [f"Observable: {x_label}"]
    if sel_label:
        detail_lines.append(f"Selection: {sel_label}")
    detail_lines.append(f"Signal strength (mu): {res['mu']}")

    if dpg.does_item_exist("discovery_title_text"):
        dpg.set_value("discovery_title_text",
                      "Discovery! The process has been observed with "
                      f"{res['sig']} sigma significance.")
    if dpg.does_item_exist("discovery_detail_text"):
        dpg.set_value("discovery_detail_text", "\n".join(detail_lines))
    if dpg.does_item_exist("discovery_process_name_input"):
        dpg.set_value("discovery_process_name_input",
                      _NAMED_PROCESSES.get(pidx, ""))
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos("discovery_window", [(vp_w - 420) // 2, (vp_h - 230) // 2])
    dpg.configure_item("discovery_window", show=True)
    dpg.focus_item("discovery_window")


def save_discovery_process_name(name: str) -> None:
    pidx = _CURRENT_DISCOVERY_PIDX[0]
    if pidx is not None:
        if name.strip():
            _NAMED_PROCESSES[pidx] = name.strip()
        _DISCOVERED_PIDS.add(pidx)
        _CURRENT_DISCOVERY_PIDX[0] = None
    _show_next_discovery()


def log_to_message_center(message_text):
    if dpg.does_item_exist("ui_console_log"):
        current_log  = dpg.get_value("ui_console_log")
        log_lines    = current_log.splitlines()
        log_lines.append(str(message_text).strip())
        if len(log_lines) > 100:
            log_lines = log_lines[-100:]
        dpg.set_value("ui_console_log", "\n".join(log_lines) + "\n")
        if dpg.does_item_exist("console_scroll_container"):
            dpg.set_y_scroll("console_scroll_container",
                             dpg.get_y_scroll_max("console_scroll_container"))


from PIL import Image
import numpy as np


def _load_png_to_texture(png_path: str, texture_tag: str) -> bool:
    """Load a PNG into a DPG dynamic texture. Returns True on success."""
    if not os.path.exists(png_path):
        return False
    try:
        img = Image.open(png_path).convert("RGBA")
        img_resized = img.resize((1272, 908), Image.Resampling.LANCZOS)
        pixel_array = np.array(img_resized, dtype=np.float32) / 255.0
        if dpg.does_item_exist(texture_tag):
            dpg.set_value(texture_tag, pixel_array.ravel().tolist())
        return True
    except Exception:
        return False


def _add_fit_label(plot_idx: int, fit_results: dict, parent: str,
                   multi_hist: bool = False) -> None:
    """Insert a fit-result text block above a plot image."""
    res = fit_results.get(plot_idx)
    if res is None:
        return
    name = _NAMED_PROCESSES.get(plot_idx) or res.get("node_name", "").strip()
    if multi_hist and name:
        dpg.add_text(f"Statistical Fit: {name}", parent=parent)
    elif multi_hist:
        dpg.add_text(f"Statistical Fit: Histogram {plot_idx + 1}", parent=parent)
    else:
        dpg.add_text("Statistical Fit", parent=parent)
    dpg.add_text(
        f"  Signal Strength (mu): {res['mu']}    Significance: {res['sig']} sigma",
        parent=parent,
    )
    dpg.add_spacer(height=2, parent=parent)


def refresh_ui_canvas(selections_info: list | None = None,
                      n_histograms: int = 1, hist_labels: list | None = None,
                      fit_results: dict | None = None):
    """Load plot PNGs into textures and rebuild the plot display group.

    selections_info: list of {"name": str, "plot_indices": [int]}, one per Selection
    branch.  When provided, plots are grouped under a collapsing header per selection
    (only when there are multiple selections).  Within each selection the plots are
    stacked without inner dropdowns.  Falls back to the legacy n_histograms /
    hist_labels behaviour when selections_info is None.
    fit_results: {plot_idx: {"mu": float, "sig": float, "node_name": str}}
    """
    if not dpg.does_item_exist("plot_display_group"):
        return

    fit_results = fit_results or {}

    # Collect all plot indices to load
    if selections_info:
        all_indices = [pi for sel in selections_info for pi in sel["plot_indices"]]
    else:
        all_indices = list(range(min(n_histograms, MAX_HIST_TEXTURES)))

    loaded = set()
    for i in all_indices:
        if i >= MAX_HIST_TEXTURES:
            continue
        png_path = os.path.join(FCE_DIR, f"hist_{i}.png")
        if _load_png_to_texture(png_path, f"plot_texture_buffer_{i}"):
            loaded.add(i)

    if not loaded:
        return

    dpg.delete_item("plot_display_group", children_only=True)

    if selections_info and len(selections_info) > 1:
        # Multiple selections: one collapsing header per selection, plots stacked inside
        for sel in selections_info:
            indices = [i for i in sel["plot_indices"] if i in loaded]
            if not indices:
                continue
            with dpg.collapsing_header(
                label=sel["name"],
                default_open=True,
                parent="plot_display_group",
            ):
                multi = len(indices) > 1
                for i in indices:
                    _add_fit_label(i, fit_results, parent=dpg.last_item(), multi_hist=multi)
                    dpg.add_image(
                        f"plot_texture_buffer_{i}",
                        tag=f"canvas_view_frame_{i}",
                        width=636, height=454,
                    )
    else:
        # Single selection (or legacy call): show plots stacked, no outer dropdown
        indices = (
            [i for i in selections_info[0]["plot_indices"] if i in loaded]
            if selections_info
            else [i for i in all_indices if i in loaded]
        )
        multi = len(indices) > 1
        if len(indices) == 1:
            _add_fit_label(indices[0], fit_results, parent="plot_display_group",
                           multi_hist=False)
            dpg.add_image(
                f"plot_texture_buffer_{indices[0]}",
                tag="canvas_view_frame_0",
                width=636, height=454,
                parent="plot_display_group",
            )
        else:
            for i in indices:
                _add_fit_label(i, fit_results, parent="plot_display_group",
                               multi_hist=multi)
                dpg.add_image(
                    f"plot_texture_buffer_{i}",
                    tag=f"canvas_view_frame_{i}",
                    width=636, height=454,
                    parent="plot_display_group",
                )


def _frame_poll_callback(sender=None, app_data=None, user_data=None):
    if not safe_get_state("running"):
        dpg.configure_item("btn_trigger", label="Run", enabled=True)
        if dpg.does_item_exist("ui_status_label"):
            dpg.set_value("ui_status_label", "")

        # Hide worker bars and release progress_ctx reference
        if dpg.does_item_exist("worker_bars_section"):
            dpg.configure_item("worker_bars_section", show=False)
        safe_set_state("progress_ctx", None)

        if safe_get_state("stop"):
            dpg.set_value("ui_progress_bar", 0.0)
            dpg.configure_item("ui_progress_bar", overlay="Aborted")
            log_to_message_center("Aborted.")
        else:
            dpg.set_value("ui_progress_bar", 1.0)
            dpg.configure_item("ui_progress_bar", overlay="Done")

            # Refresh plots with fit results overlaid above each image
            fit_results = safe_get_state("fit_results")
            sel_info = getattr(_frame_poll_callback, "_last_selections_info", None)
            n        = getattr(_frame_poll_callback, "_last_n_hist", 1)
            labels   = getattr(_frame_poll_callback, "_last_hist_labels", None)
            refresh_ui_canvas(selections_info=sel_info, n_histograms=n,
                              hist_labels=labels, fit_results=fit_results)
            log_to_message_center("Completed.")

            # Discovery popup for new 5-sigma results (skip already-discovered)
            to_discover = [
                (pidx, res) for pidx, res in fit_results.items()
                if res.get("sig") is not None and res["sig"] >= 5.0
                and pidx not in _DISCOVERED_PIDS
            ]
            if to_discover:
                _DISCOVERY_QUEUE.clear()
                _DISCOVERY_QUEUE.extend(to_discover)
                _show_next_discovery()

        # Apply final node colour states: completed nodes stay green;
        # nodes that were still active when stopped turn red.
        active    = safe_get_state("active_nodes")
        completed = safe_get_state("completed_nodes")
        was_stopped = safe_get_state("stop")
        apply_node_runtime_states(active, completed, stopped=was_stopped)
        return

    prog   = safe_get_state("progress")
    status = safe_get_state("status_msg")
    phase  = safe_get_state("current_phase")
    if status:
        log_to_message_center(status)
        safe_set_state("status_msg", "")

    # Elapsed time since run started
    start   = safe_get_state("run_start_time")
    elapsed = int(time.time() - start) if start > 0 else 0
    elapsed_str = f"{elapsed // 60}:{elapsed % 60:02d}"

    pct = int(prog * 100)
    dpg.set_value("ui_progress_bar", prog)
    dpg.configure_item("ui_progress_bar", overlay=f"{pct}%")

    # Status label: phase name + elapsed time
    if dpg.does_item_exist("ui_status_label"):
        label_text = f"{phase}  ({elapsed_str})" if phase else f"Processing...  ({elapsed_str})"
        dpg.set_value("ui_status_label", label_text)

    # Apply node colour highlights from background state
    active    = safe_get_state("active_nodes")
    completed = safe_get_state("completed_nodes")
    apply_node_runtime_states(active, completed)

    # ── Update per-worker progress bars ──────────────────────────────────────
    ctx = safe_get_state("progress_ctx")
    if ctx is not None and dpg.does_item_exist("worker_bars_section"):
        n_w = ctx.get("n_workers", 1)
        if n_w > 1:
            with ctx["slot_lock"]:
                w_snapshot = dict(ctx["worker_data"])
            for _slot in range(n_w):
                bar_tag = f"worker_bar_{_slot}"
                lbl_tag = f"worker_label_{_slot}"
                if not dpg.does_item_exist(bar_tag):
                    continue
                if _slot in w_snapshot:
                    wd = w_snapshot[_slot]
                    ratio = wd["done"] / wd["total"] if wd["total"] > 0 else 0.0
                    dpg.set_value(lbl_tag, f"Worker {_slot + 1}:  {wd['sample']}")
                    dpg.set_value(bar_tag, ratio)
                    dpg.configure_item(bar_tag, overlay=f"{int(ratio * 100)}%")
                else:
                    dpg.set_value(lbl_tag, f"Worker {_slot + 1}:  --")
                    dpg.set_value(bar_tag, 0.0)
                    dpg.configure_item(bar_tag, overlay="")

    dpg.set_frame_callback(dpg.get_frame_count() + 6, _frame_poll_callback)


def trigger_analysis_pipeline():
    global CURRENT_WORKER
    from run_engine import execute_analysis
    from ui.state import REGISTRY

    if safe_get_state("running"):
        safe_set_state("stop", True)
        dpg.configure_item("btn_trigger", label="Stopping...", enabled=False)
        return

    _OBS_TYPES = {"Observable", "ObsGlobal", "ObsObject", "ObsVectorSum", "ObsCustom"}
    present  = set(REGISTRY.nodes.values())
    missing  = []
    for t in ["DataSource", "Multiplicity", "Selection", "Histogram"]:
        if t not in present:
            missing.append(t)
    if not (present & _OBS_TYPES):
        missing.append("Observable")
    if missing:
        log_to_message_center(
            f"Pipeline incomplete — missing: {', '.join(missing)}"
        )
        return

    # Clear errors from any previous run; preserve runtime (green) states until
    # we know which nodes need reprocessing (determined after topology compile).
    clear_all_node_errors()

    # Check connectivity
    error_nids = check_pipeline_connectivity()
    all_nids   = list(REGISTRY.nodes.keys())
    if error_nids:
        mark_nodes_from_pipeline_check(error_nids, all_nids)
        log_to_message_center("Pipeline has unconnected nodes (highlighted in red).")
        return

    # Validate expression syntax
    expr_errors = validate_node_expressions()
    if expr_errors:
        for nid, msg in expr_errors:
            from ui.graph import _set_node_error
            _set_node_error(nid, True, msg)
            log_to_message_center(f"Error: {msg}")
        return

    if CURRENT_WORKER and CURRENT_WORKER.is_alive():
        safe_set_state("stop", True)
        CURRENT_WORKER.join(timeout=1.5)
        if CURRENT_WORKER.is_alive():
            return

    # Reset fit results from previous run
    safe_set_state("fit_mu",      None)
    safe_set_state("fit_sig",     None)
    safe_set_state("fit_results", {})

    safe_set_state("progress",       0.0)
    safe_set_state("running",        True)
    safe_set_state("stop",           False)
    safe_set_state("current_phase",  "Starting...")
    safe_set_state("run_start_time", time.time())

    cfg = compile_graph_topology()

    # Build a sample-key → process-name map from ALL named histograms so every
    # plot's legend can reflect all discovered processes, not just its own.
    _all_hcfgs = list(cfg.get("histograms", []))
    for _sel in cfg.get("selections", []):
        _all_hcfgs.extend(_sel.get("histograms", []))
    _proc_map: dict[str, str] = {}
    for _hcfg in _all_hcfgs:
        _pidx = _hcfg.get("plot_idx", 0)
        _tgt  = _hcfg.get("target", "")
        if _tgt and _pidx in _NAMED_PROCESSES:
            _proc_map[_tgt] = _NAMED_PROCESSES[_pidx]
        if _pidx in _NAMED_PROCESSES:
            _hcfg["process_name"] = _NAMED_PROCESSES[_pidx]
    if _proc_map:
        for _hcfg in _all_hcfgs:
            _hcfg["process_names_map"] = _proc_map

    # Determine which selection caches are still valid so we can keep those
    # nodes green and only reset the ones that need reprocessing.
    _cached_sel_nids = set()
    try:
        import json as _json
        _hdir = FCE_DIR
        _config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "samples.json"
        )
        with open(_config_path) as _f:
            _sdata = _json.load(_f)
        _en = cfg["energy"].replace(" GeV", "")
        _active = list(_sdata.get(_en, {}).keys())
        for _sel in cfg.get("selections", []):
            _nid = _sel.get("nid")
            if _nid is None:
                continue
            _h5 = _sel["h5_sel"]
            if _active and all(
                os.path.exists(os.path.join(_hdir, "cache", f"sel_{_h5}_{_s}.npz"))
                for _s in _active
            ):
                _cached_sel_nids.add(_nid)
    except Exception:
        pass

    # DataSource and Multiplicity are config-only; they're always "done".
    _always_done = {nid for nid, ntype in REGISTRY.nodes.items()
                    if ntype in ("DataSource", "Multiplicity")}
    _pre_done = set(_cached_sel_nids) | _always_done

    # Reset runtime themes: clear non-pre-done nodes, keep pre-done ones green
    for _nid in list(REGISTRY.nodes.keys()):
        if _nid in _pre_done:
            _set_node_done(_nid)
        else:
            _clear_node_runtime_theme(_nid)

    # Seed RUN_STATE node sets
    safe_set_state("active_nodes",    set())
    safe_set_state("completed_nodes", _pre_done)

    # ── Show / reset per-worker bars if running with multiple workers ─────────
    _n_w = safe_get_state("n_workers")
    if dpg.does_item_exist("worker_bars_section"):
        if _n_w > 1:
            dpg.configure_item("worker_bars_section", show=True)
            for _wi in range(8):
                row_tag = f"worker_bar_row_{_wi}"
                if dpg.does_item_exist(row_tag):
                    dpg.configure_item(row_tag, show=(_wi < _n_w))
                if dpg.does_item_exist(f"worker_label_{_wi}"):
                    dpg.set_value(f"worker_label_{_wi}", f"Worker {_wi + 1}:  --")
                if dpg.does_item_exist(f"worker_bar_{_wi}"):
                    dpg.set_value(f"worker_bar_{_wi}", 0.0)
                    dpg.configure_item(f"worker_bar_{_wi}", overlay="")
        else:
            dpg.configure_item("worker_bars_section", show=False)

    # Build selections_info for the display refresh after run
    selections = cfg.get("selections", [])
    if selections:
        selections_info = []
        for sel in selections:
            name = sel.get("node_name", "").strip()
            if not name:
                name = f"Selection {len(selections_info) + 1}"
            plot_indices = [h["plot_idx"] for h in sel.get("histograms", [])]
            selections_info.append({"name": name, "plot_indices": plot_indices})
        _frame_poll_callback._last_selections_info = selections_info
        _frame_poll_callback._last_n_hist = sum(
            len(s["plot_indices"]) for s in selections_info
        )
        _frame_poll_callback._last_hist_labels = None
    else:
        histograms = cfg.get("histograms", [])
        _frame_poll_callback._last_selections_info = None
        _frame_poll_callback._last_n_hist = max(1, len(histograms))
        hist_labels = []
        for i, hcfg in enumerate(histograms):
            name = hcfg.get("node_name", "").strip()
            hist_labels.append(name if name else f"Histogram {i + 1}")
        _frame_poll_callback._last_hist_labels = hist_labels

    dpg.configure_item("btn_trigger", label="Stop (Processing..)", enabled=True)

    CURRENT_WORKER = threading.Thread(target=execute_analysis, args=(cfg, None), daemon=True)
    CURRENT_WORKER.start()

    dpg.set_frame_callback(dpg.get_frame_count() + 6, _frame_poll_callback)


def _download_state_poll(sender=None, app_data=None, user_data=None):
    from ui.state import DOWNLOAD_LOG_QUEUE
    while not DOWNLOAD_LOG_QUEUE.empty():
        log_to_message_center(DOWNLOAD_LOG_QUEUE.get_nowait())

    from ui.state import get_download_running
    if get_download_running():
        dpg.set_frame_callback(dpg.get_frame_count() + 6, _download_state_poll)
    else:
        log_to_message_center("Download finished.")


_PENDING_DOWNLOAD = (None, None)  # (detector, energy_gev) waiting for confirmation


def _download_worker_thread(detector, energy_gev, force=False):
    from run_engine import run_dataset_download
    from ui.state import DOWNLOAD_LOG_QUEUE, set_download_running
    try:
        for log_line in run_dataset_download(detector=detector, energy_gev=energy_gev, force=force):
            DOWNLOAD_LOG_QUEUE.put(log_line)
    finally:
        set_download_running(False)


def _data_exists(detector, energy_gev):
    """Return True if any local dataset files match the given filter."""
    datasets_dir = os.path.join(FCE_DIR, "datasets")
    if detector and energy_gev:
        check = os.path.join(datasets_dir, detector, f"{energy_gev}GeV")
    elif detector:
        check = os.path.join(datasets_dir, detector)
    else:
        check = datasets_dir
    if not os.path.isdir(check):
        return False
    for _root, _dirs, _files in os.walk(check):
        if _files:
            return True
    return False


def _start_download(detector, energy_gev, force=False):
    from ui.state import DOWNLOAD_LOG_QUEUE, get_download_running, set_download_running
    if get_download_running():
        log_to_message_center("A download is already in progress. Please wait.")
        return
    while not DOWNLOAD_LOG_QUEUE.empty():
        DOWNLOAD_LOG_QUEUE.get_nowait()
    label = f"{detector or 'All'} / {energy_gev + ' GeV' if energy_gev else 'All'}"
    log_to_message_center(f"{'Re-downloading' if force else 'Downloading'}: {label}")
    set_download_running(True)  # set before thread starts to avoid race with poll
    threading.Thread(
        target=_download_worker_thread,
        args=(detector, energy_gev, force),
        daemon=True,
    ).start()
    dpg.set_frame_callback(dpg.get_frame_count() + 6, _download_state_poll)


def confirm_redownload(sender=None, app_data=None, user_data=None):
    """Called by the Yes button in the re-download confirmation popup."""
    global _PENDING_DOWNLOAD
    dpg.configure_item("redownload_confirm_window", show=False)
    det, en = _PENDING_DOWNLOAD
    _PENDING_DOWNLOAD = (None, None)
    _start_download(det, en, force=True)


def trigger_dataset_download(sender=None, app_data=None, user_data=None):
    """user_data = (detector, energy_gev) or None for full download."""
    global _PENDING_DOWNLOAD

    detector   = None
    energy_gev = None
    if isinstance(user_data, (tuple, list)) and len(user_data) == 2:
        detector, energy_gev = user_data

    if _data_exists(detector, energy_gev):
        _PENDING_DOWNLOAD = (detector, energy_gev)
        label = f"{detector or 'All'} / {energy_gev + ' GeV' if energy_gev else 'All'}"
        if dpg.does_item_exist("redownload_confirm_text"):
            dpg.set_value("redownload_confirm_text",
                          f"Data for {label} is already downloaded.\n"
                          "Do you want to re-download it?")
        if dpg.does_item_exist("redownload_confirm_window"):
            vp_w = dpg.get_viewport_width()
            vp_h = dpg.get_viewport_height()
            dpg.set_item_pos("redownload_confirm_window",
                             [(vp_w - 380) // 2, (vp_h - 130) // 2])
            dpg.configure_item("redownload_confirm_window", show=True)
            dpg.focus_item("redownload_confirm_window")
        return

    _start_download(detector, energy_gev, force=False)
