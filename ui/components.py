import os
import threading
import dearpygui.dearpygui as dpg
from ui.graph import (compile_graph_topology, check_pipeline_connectivity,
                      mark_nodes_from_pipeline_check, validate_node_expressions,
                      clear_all_node_errors)
from ui.state import get_run_state, update_run_state
from paths import get_fce_home

safe_get_state = get_run_state
safe_set_state = update_run_state

FCE_DIR = get_fce_home()
CURRENT_WORKER = None


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


def refresh_ui_canvas():
    img_path = os.path.join(FCE_DIR, "hist.png")
    if os.path.exists(img_path):
        try:
            img = Image.open(img_path).convert("RGBA")
            img_resized = img.resize((1272, 908), Image.Resampling.LANCZOS)
            pixel_array = np.array(img_resized, dtype=np.float32) / 255.0
            if dpg.does_item_exist("plot_texture_buffer"):
                dpg.set_value("plot_texture_buffer", pixel_array.ravel().tolist())
        except Exception:
            pass


def _frame_poll_callback(sender=None, app_data=None, user_data=None):
    if not safe_get_state("running"):
        dpg.configure_item("btn_trigger", label="Run", enabled=True)
        if safe_get_state("stop"):
            dpg.set_value("ui_progress_bar", 0.0)
            dpg.configure_item("ui_progress_bar", overlay="Aborted")
            log_to_message_center("Aborted.")
        else:
            dpg.set_value("ui_progress_bar", 1.0)
            dpg.configure_item("ui_progress_bar", overlay="100%")
            refresh_ui_canvas()
            log_to_message_center("Completed.")

            # Update fit results if available
            mu  = safe_get_state("fit_mu")
            sig = safe_get_state("fit_sig")
            if mu is not None and dpg.does_item_exist("ui_txt_mu"):
                dpg.set_value("ui_txt_mu", f"Best Fit Signal Strength: {mu}")
            if sig is not None and dpg.does_item_exist("ui_txt_sig"):
                dpg.set_value("ui_txt_sig", f"Discovery Significance: {sig} sigma")
        return

    prog   = safe_get_state("progress")
    status = safe_get_state("status_msg")
    if status:
        log_to_message_center(status)
        safe_set_state("status_msg", "")

    pct = int(prog * 100)
    dpg.set_value("ui_progress_bar", prog)
    dpg.configure_item("ui_progress_bar", overlay=f"{pct}%")

    dpg.set_frame_callback(dpg.get_frame_count() + 6, _frame_poll_callback)


def trigger_analysis_pipeline():
    global CURRENT_WORKER
    from run_engine import execute_analysis
    from ui.state import REGISTRY

    if safe_get_state("running"):
        safe_set_state("stop", True)
        dpg.configure_item("btn_trigger", label="Stopping...", enabled=False)
        return

    required = ["DataSource", "Multiplicity", "Selection", "Observable", "Histogram"]
    present  = set(REGISTRY.nodes.values())
    missing  = [t for t in required if t not in present]
    if missing:
        log_to_message_center(
            f"Pipeline incomplete — missing: {', '.join(missing)}"
        )
        return

    # Clear errors from any previous run
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
    safe_set_state("fit_mu",  None)
    safe_set_state("fit_sig", None)
    if dpg.does_item_exist("ui_txt_mu"):
        dpg.set_value("ui_txt_mu", "Best Fit Parameter: N/A")
    if dpg.does_item_exist("ui_txt_sig"):
        dpg.set_value("ui_txt_sig", "Discovery Significance: N/A")

    safe_set_state("progress", 0.0)
    safe_set_state("running",  True)
    safe_set_state("stop",     False)

    cfg = compile_graph_topology()
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
