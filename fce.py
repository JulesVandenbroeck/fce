import os
import sys
import shutil

# Ensure the parent of fce_studio/ is on sys.path so the package is importable
# whether fce.py is run directly or via the installed `fce` entry point.
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

# Must run before dearpygui is imported: it redirects XDG_CACHE_HOME away
# from unwritable locations before Mesa creates its GL context and tries
# to set up a shader cache there.
from paths import get_fce_home, configure_cache_env
configure_cache_env()

import numpy as np
import dearpygui.dearpygui as dpg
from PIL import Image

from ui.graph import (link_callback, delink_callback, create_node,
                      setup_link_handlers, on_node_editor_drop,
                      save_pipeline, load_pipeline,
                      create_node_below_lowest, delete_unconnected_nodes)
from ui.state import REGISTRY
from ui.components import (trigger_analysis_pipeline, trigger_dataset_download,
                           confirm_redownload, MAX_HIST_TEXTURES,
                           save_discovery_process_name)
from ui.state import update_run_state as _set_state
from ui.tutorial import show_tutorial
import ui.state as _ui_state
from fce_studio import __version__

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Clear output from previous sessions ───────────────────────────────────────
# Selection caches (cache/*.npz) are content-addressed by a hash of the
# expressions and settings, so they are safe to reuse across sessions and are
# NOT wiped here.  Only the positional output files (hist{N}_*.root) are wiped
# because they use index-based names that can mis-match a new graph layout.
# Best-effort: a stale/unwritable directory must never block startup.
try:
    _FCE_DIR = get_fce_home()
    for _cache_subdir in ("cache", "output"):
        _d = os.path.join(_FCE_DIR, _cache_subdir)
        try:
            if _cache_subdir == "output" and os.path.exists(_d):
                shutil.rmtree(_d)
            os.makedirs(_d, exist_ok=True)
        except OSError:
            pass
except OSError:
    pass

dpg.create_context()

# ── Textures ─────────────────────────────────────────────────────────────────
with dpg.texture_registry():
    empty_buffer = [0.1, 0.1, 0.1, 1.0] * (1272 * 908)
    for _ti in range(MAX_HIST_TEXTURES):
        dpg.add_dynamic_texture(
            width=1272, height=908,
            default_value=empty_buffer,
            tag=f"plot_texture_buffer_{_ti}",
        )

    # Logo for About window
    _logo_loaded = False
    for _ext in ("fce.ico", "fce.svg"):
        _logo_path = os.path.join(_HERE, _ext)
        if os.path.exists(_logo_path):
            try:
                _img = Image.open(_logo_path).convert("RGBA")
                _img.thumbnail((80, 80), Image.Resampling.LANCZOS)
                _canvas = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
                _canvas.paste(_img, ((80 - _img.width) // 2, (80 - _img.height) // 2))
                _logo_buf = (np.array(_canvas, dtype=np.float32) / 255.0).ravel().tolist()
                dpg.add_dynamic_texture(80, 80, _logo_buf, tag="fce_logo_texture")
                _logo_loaded = True
                break
            except Exception:
                pass

# ── Font registry ─────────────────────────────────────────────────────────────
_large_font = None
_extended_font = None
with dpg.font_registry():
    _font_candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for _fp in _font_candidates:
        if os.path.exists(_fp):
            try:
                _large_font = dpg.add_font(_fp, 20)
                # Smaller font with Dingbats range so ✏ (U+270F) and ✔ (U+2714)
                # render correctly on node name buttons.
                with dpg.font(_fp, 13) as _extended_font:
                    pass
                break
            except Exception:
                pass

_ui_state.EXTENDED_FONT = _extended_font
_ui_state.LARGE_FONT = _large_font

# ── Help popup (expression guide) ─────────────────────────────────────────────
with dpg.window(tag="help_expr_window", label="Expression Guide",
                modal=True, show=False, width=520, height=420,
                no_resize=False):
    dpg.add_text(
        "Variables  (pt-sorted within type)\n\n"
        "  Counts :  nlep  nel  nmu  njets  nphot\n\n"
        "  Leptons:  l1.pt  l1.eta  l1.phi  l1.e  l1.d0  l1.z0  l1.p4\n"
        "            l2.pt  l2.eta  l2.phi  l2.e  l2.d0  l2.z0  l2.p4\n\n"
        "  Jets   :  j1.pt  j1.eta  j1.phi  j1.e  j1.btag  j1.p4\n"
        "            j2.pt  j2.eta  j2.phi  j2.e  j2.btag  j2.p4\n\n"
        "  Photons:  ph1.pt  ph1.eta  ph1.phi  ph1.e  ph1.p4\n"
        "            ph2.pt  ph2.eta  ph2.phi  ph2.e  ph2.p4\n\n"
        "  MET    :  met.pt  met.eta  met.phi  met.e  met.p4\n\n"
        "4-vector arithmetic (p4 objects)\n\n"
        "  (l1.p4 + l2.p4).mass   →  invariant mass\n"
        "  (l1.p4 + l2.p4).pt     →  system pT\n"
        "  l1.p4.deltaR(l2.p4)    →  ΔR\n"
        "  deltaR(l1, l2)          →  ΔR via eta/phi\n"
        "  l1.pt + l2.pt           →  sum pT\n\n"
        "Operators :  > < >= <= == !=\n"
        "Logic     :  and  or  not  ( )"
    )
    dpg.add_spacer(height=8)
    dpg.add_button(
        label="Close",
        callback=lambda: dpg.configure_item("help_expr_window", show=False),
        width=100,
    )

# ── Re-download confirmation window ───────────────────────────────────────────
with dpg.window(tag="redownload_confirm_window", label="Confirm Re-download",
                modal=True, show=False, width=380, height=130, no_resize=True):
    dpg.add_text("", tag="redownload_confirm_text", wrap=360)
    dpg.add_spacer(height=10)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Yes, re-download", tag="redownload_yes_btn",
                       callback=confirm_redownload, width=160)
        dpg.add_spacer(width=10)
        dpg.add_button(label="Cancel", width=80,
                       callback=lambda: dpg.configure_item("redownload_confirm_window", show=False))

# ── About window ──────────────────────────────────────────────────────────────
with dpg.window(tag="about_window", label="About",
                modal=True, show=False, width=360, height=280,
                no_resize=True):
    dpg.add_spacer(height=6)
    if _logo_loaded:
        dpg.add_image("fce_logo_texture")
    dpg.add_spacer(height=6)
    dpg.add_text("Future Collider Experiment Studio", tag="about_title")
    dpg.add_text(f"Version:  {__version__}")
    dpg.add_separator()
    dpg.add_text("Author:   Kirill Skovpen")
    dpg.add_text("Email:    Kirill.Skovpen@cern.ch")
    dpg.add_separator()
    dpg.add_spacer(height=8)
    dpg.add_button(
        label="Close",
        callback=lambda: dpg.configure_item("about_window", show=False),
        width=100,
    )

# ── Node error detail window ──────────────────────────────────────────────────
with dpg.window(tag="node_error_window", label="Node Error",
                modal=True, show=False, width=440, height=160, no_resize=False):
    dpg.add_text("", tag="node_error_text", wrap=420)
    dpg.add_spacer(height=8)
    dpg.add_button(
        label="Close",
        callback=lambda s, a, u: dpg.configure_item("node_error_window", show=False),
        width=80,
    )

# ── Discovery popup ──────────────────────────────────────────────────────────
with dpg.window(tag="discovery_window", label="*** DISCOVERY ***",
                modal=True, show=False, width=460, height=320,
                no_resize=True):
    dpg.add_spacer(height=8)
    dpg.add_text("", tag="discovery_title_text", wrap=440)
    dpg.add_spacer(height=4)
    dpg.add_text("", tag="discovery_detail_text", wrap=440)
    dpg.add_spacer(height=4)
    dpg.add_text("", tag="discovery_hint_text", wrap=440, color=(180, 220, 180))
    dpg.add_separator()
    dpg.add_spacer(height=4)
    dpg.add_text(
        "Statistical fit terms:\n"
        "  Signal strength (mu): ratio of observed to predicted yield.\n"
        "    mu = 1 means perfect agreement with the Standard Model.\n"
        "  Significance (sigma): how many s.d. above the background-only\n"
        "    hypothesis. 5 sigma is the particle physics discovery threshold.",
        wrap=440, color=(160, 160, 160),
    )
    dpg.add_spacer(height=8)
    dpg.add_text("Name this process:")
    dpg.add_input_text(tag="discovery_process_name_input", width=-1,
                       hint="e.g. Z boson, Higgs boson")
    dpg.add_spacer(height=8)
    with dpg.group(horizontal=True):
        dpg.add_button(
            label="Confirm Name",
            tag="discovery_confirm_btn",
            callback=lambda: save_discovery_process_name(
                dpg.get_value("discovery_process_name_input")
            ),
            width=200, height=30,
        )
        dpg.add_spacer(width=8)
        dpg.add_button(
            label="Skip",
            callback=lambda: save_discovery_process_name(""),
            width=100, height=30,
        )


# ── Window show helpers ───────────────────────────────────────────────────────

def _on_save_pipeline(sender, app_data):
    path = (app_data or {}).get("file_path_name", "").strip()
    if not path:
        return
    if not path.lower().endswith(".json"):
        path += ".json"
    from ui.components import log_to_message_center
    try:
        save_pipeline(path)
        log_to_message_center(f"Pipeline saved: {path}")
    except Exception as e:
        log_to_message_center(f"Save failed: {e}")


def _on_load_pipeline(sender, app_data):
    path = (app_data or {}).get("file_path_name", "").strip()
    if not path or not os.path.exists(path):
        return
    from ui.components import log_to_message_center
    try:
        load_pipeline(path)
        log_to_message_center(f"Pipeline loaded: {path}")
    except Exception as e:
        log_to_message_center(f"Load failed: {e}")


# ── File dialogs (Save / Load pipeline) ──────────────────────────────────────
with dpg.file_dialog(
    directory_selector=False, show=False,
    callback=_on_save_pipeline, width=700, height=400,
    tag="save_pipeline_dialog", modal=True,
    default_filename="pipeline.json",
):
    dpg.add_file_extension(".json", color=(255, 255, 100, 255))
    dpg.add_file_extension("", color=(150, 150, 150, 255))

with dpg.file_dialog(
    directory_selector=False, show=False,
    callback=_on_load_pipeline, width=700, height=400,
    tag="load_pipeline_dialog", modal=True,
):
    dpg.add_file_extension(".json", color=(255, 255, 100, 255))
    dpg.add_file_extension("", color=(150, 150, 150, 255))


_EXERCISES_TEXT = (
    "Suggested exercise progression\n\n"
    "Exercise 1 - Count leptons (Getting started)\n"
    "  Goal  : Plot the lepton multiplicity distribution.\n"
    "  Setup : Observable = Global > nlep, Histogram bins=6 range 0-6.\n"
    "  Learn : How many leptons does each process typically produce?\n\n"
    "Exercise 2 - Z boson mass peak (Resonance search)\n"
    "  Goal  : Observe the Z boson as a peak in the di-lepton mass.\n"
    "  Setup : Multiplicity >= 2 leptons; Observable = Vec Sum > mass\n"
    "          (l1 + l2); Histogram bins=50 range 60-120 GeV.\n"
    "  Learn : What is the Z boson mass? What is the peak width?\n\n"
    "Exercise 3 - Lepton pT cut (Improving signal purity)\n"
    "  Goal  : Reduce background by requiring hard leptons.\n"
    "  Setup : Add Selection l1.pt > 20 and l2.pt > 10 after Multiplicity.\n"
    "  Learn : How does the cut change the signal-to-background ratio?\n\n"
    "Exercise 4 - Statistical fit (Discovery)\n"
    "  Goal  : Perform a hypothesis test to claim discovery.\n"
    "  Setup : Set Fit Signal in the Histogram node (e.g. Zee or Zmumu),\n"
    "          then Run. Check significance and signal strength (mu).\n"
    "  Learn : Is significance >= 5 sigma? Is mu consistent with 1?\n\n"
    "Exercise 5 - Higher energy (Higgs search)\n"
    "  Goal  : Search for Higgs-associated production at 240 or 365 GeV.\n"
    "  Setup : Change Data node energy to 240 GeV; use Vec Sum mass\n"
    "          of the two leptons or the recoil system.\n"
    "  Learn : At what mass do you find a new excess?\n\n"
    "Tip: Save your pipeline (File > Save Pipeline) to resume later."
)

_EXERCISES_W = 580
_EXERCISES_H = 480


def _show_exercises_window(sender=None, app_data=None, user_data=None):
    if not dpg.does_item_exist("exercises_window"):
        with dpg.window(
            tag="exercises_window",
            label="Suggested Exercises",
            modal=False, show=False,
            width=_EXERCISES_W, height=_EXERCISES_H,
            no_resize=False, no_collapse=True,
        ):
            with dpg.child_window(width=-1, height=-40, border=False):
                dpg.add_spacer(height=6)
                dpg.add_text(_EXERCISES_TEXT, wrap=_EXERCISES_W - 24)
            dpg.add_separator()
            dpg.add_spacer(height=6)
            dpg.add_button(
                label="Close", width=90,
                callback=lambda: dpg.configure_item("exercises_window", show=False),
            )
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos("exercises_window",
                     [(vp_w - _EXERCISES_W) // 2, (vp_h - _EXERCISES_H) // 2])
    dpg.configure_item("exercises_window", show=True)
    dpg.focus_item("exercises_window")


def _show_about_window(sender=None, app_data=None, user_data=None):
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos("about_window", [(vp_w - 360) // 2, (vp_h - 280) // 2])
    dpg.configure_item("about_window", show=True)


_PALETTE_HELP_W = 520
_PALETTE_HELP_H = 320

_PALETTE_HELP_TEXT = (
    "The palette at the bottom of the canvas lets you add nodes.\n\n"
    "CLICK  -- creates a new node below the lowest existing node of\n"
    "          the same type (or at a default position if none exist).\n\n"
    "DRAG   -- drag a button onto the canvas to place the node at\n"
    "          the exact drop position.\n\n"
    "Node types available:\n"
    "  Multiplicity -- filter events by minimum object counts\n"
    "  Selection    -- filter events with a boolean expression\n"
    "  Observable   -- click to reveal four sub-types:\n"
    "                    Global, Object, Vec Sum, Custom\n"
    "  Histogram    -- set bins, range and optional signal for fitting\n\n"
    "DELETE UNCONNECTED (right side) -- removes all nodes that have\n"
    "no connections. The Data node is never deleted.\n\n"
    "Nodes can also be added via 'Add Node' in the top menu bar."
)


def _show_palette_help(sender=None, app_data=None, user_data=None):
    if not dpg.does_item_exist("palette_help_window"):
        with dpg.window(
            tag="palette_help_window",
            label="Node Palette - Help",
            modal=False, show=False,
            width=_PALETTE_HELP_W, height=_PALETTE_HELP_H,
            no_resize=True, no_collapse=True,
            no_scrollbar=True,
        ):
            with dpg.child_window(width=-1, height=-40, border=False):
                dpg.add_spacer(height=6)
                dpg.add_text(_PALETTE_HELP_TEXT, wrap=_PALETTE_HELP_W - 24)
            dpg.add_separator()
            dpg.add_spacer(height=6)
            dpg.add_button(
                label="Close", width=90,
                callback=lambda: dpg.configure_item("palette_help_window", show=False),
            )
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos("palette_help_window",
                     [(vp_w - _PALETTE_HELP_W) // 2,
                      (vp_h - _PALETTE_HELP_H) // 2])
    dpg.configure_item("palette_help_window", show=True)
    dpg.focus_item("palette_help_window")


def _show_obs_submenu(sender=None, app_data=None, user_data=None):
    dpg.configure_item("palette_main_grp", show=False)
    dpg.configure_item("palette_obs_grp", show=True)


def _show_main_palette(sender=None, app_data=None, user_data=None):
    dpg.configure_item("palette_obs_grp", show=False)
    dpg.configure_item("palette_main_grp", show=True)


# ── Main window ───────────────────────────────────────────────────────────────
with dpg.window(tag="primary_studio_window", label="Future Collider Experiment"):

    with dpg.viewport_menu_bar():

        with dpg.menu(label="File"):
            dpg.add_menu_item(
                label="Save Pipeline...",
                callback=lambda: dpg.configure_item("save_pipeline_dialog", show=True),
            )
            dpg.add_menu_item(
                label="Load Pipeline...",
                callback=lambda: dpg.configure_item("load_pipeline_dialog", show=True),
            )
            dpg.add_separator()
            dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())

        # Data menu: per-detector/energy downloads
        with dpg.menu(label="Data"):
            with dpg.menu(label="Download"):
                dpg.add_menu_item(
                    label="All",
                    callback=trigger_dataset_download,
                    user_data=(None, None),
                )
                dpg.add_separator()
                for _det in ("IDEA", "CLD"):
                    with dpg.menu(label=_det):
                        for _en in ("91", "160", "240", "365"):
                            dpg.add_menu_item(
                                label=f"{_en} GeV",
                                callback=trigger_dataset_download,
                                user_data=(_det, _en),
                            )

        with dpg.menu(label="Add Node"):
            dpg.add_menu_item(
                label="Multiplicity",
                callback=lambda: create_node("Multiplicity"),
            )
            dpg.add_menu_item(
                label="Selection",
                callback=lambda: create_node("Selection"),
            )
            with dpg.menu(label="Observable"):
                dpg.add_menu_item(
                    label="Global",
                    callback=lambda: create_node("ObsGlobal"),
                )
                dpg.add_menu_item(
                    label="Object",
                    callback=lambda: create_node("ObsObject"),
                )
                dpg.add_menu_item(
                    label="Vector Sum",
                    callback=lambda: create_node("ObsVectorSum"),
                )
                dpg.add_menu_item(
                    label="Custom",
                    callback=lambda: create_node("ObsCustom"),
                )
            dpg.add_menu_item(
                label="Histogram",
                callback=lambda: create_node("Histogram"),
            )

        with dpg.menu(label="About"):
            dpg.add_menu_item(
                label="About FCE Studio...",
                callback=_show_about_window,
            )

        with dpg.menu(label="Help"):
            dpg.add_menu_item(
                label="Tutorial...",
                callback=show_tutorial,
            )
            dpg.add_menu_item(
                label="Exercises...",
                callback=lambda: _show_exercises_window(),
            )

    # ── Layout: node editor (left) + control panel with console (right) ───
    with dpg.group(horizontal=True):

        # Left: node editor (leaves room for palette at bottom)
        with dpg.child_window(width=-670, height=-85, border=False,
                              tag="node_editor_pane",
                              drop_callback=on_node_editor_drop):
            with dpg.node_editor(
                tag="node_editor_container",
                callback=link_callback,
                delink_callback=delink_callback,
                width=-1,
                height=-1,
            ):
                pass

        # Right: controls + plot + console
        with dpg.child_window(width=660, height=-85, border=False):

            dpg.add_spacer(height=18)
            dpg.add_progress_bar(
                label="Progress",
                tag="ui_progress_bar",
                default_value=0.0,
                overlay="Ready",
                width=-1,
                height=22,
            )
            dpg.add_text(
                "",
                tag="ui_status_label",
                color=(155, 155, 155),
            )
            dpg.add_spacer(height=4)

            # ── Worker count control ───────────────────────────────────────
            with dpg.group(horizontal=True):
                dpg.add_text("Workers:", color=(200, 200, 200))
                dpg.add_spacer(width=4)
                dpg.add_input_int(
                    tag="ui_worker_count",
                    default_value=4,
                    min_value=1,
                    max_value=8,
                    min_clamped=True,
                    max_clamped=True,
                    width=70,
                    callback=lambda s, a, u: _set_state("n_workers", max(1, min(a, 8))),
                )

            # ── Per-worker progress bars (shown only when n_workers > 1) ──
            # Pre-create 8 rows; trigger_analysis_pipeline shows the right count.
            with dpg.group(tag="worker_bars_section", show=False):
                dpg.add_spacer(height=2)
                for _wi in range(8):
                    with dpg.group(tag=f"worker_bar_row_{_wi}",
                                   horizontal=True, show=False):
                        dpg.add_text(
                            f"Worker {_wi + 1}:  --",
                            tag=f"worker_label_{_wi}",
                            color=(180, 180, 180),
                        )
                        dpg.add_progress_bar(
                            tag=f"worker_bar_{_wi}",
                            default_value=0.0,
                            overlay="",
                            width=-1,
                            height=14,
                        )
                dpg.add_spacer(height=2)

            dpg.add_spacer(height=4)
            dpg.add_button(
                label="Run",
                tag="btn_trigger",
                callback=trigger_analysis_pipeline,
                width=-1,
                height=42,
            )
            dpg.add_spacer(height=5)
            dpg.add_spacer(height=6)
            with dpg.group(tag="plot_display_group"):
                dpg.add_image(
                    "plot_texture_buffer_0",
                    tag="canvas_view_frame_0",
                    width=636,
                    height=454,
                )
            with dpg.child_window(
                tag="console_scroll_container",
                width=-1,
                height=-1,    # fills all remaining height
                border=False,
            ):
                dpg.add_text(
                    tag="ui_console_log",
                    default_value="Initialized.\n",
                    wrap=0,
                )

    # ── Node palette (bottom bar) — must be inside the primary window ─────
    # Height 80 px, no_scrollbar prevents any overflow scroll.
    # A two-column borderless table separates node-creation buttons (left,
    # stretching) from the Delete Unconnected button (right, fixed width).
    # Vertical centering: child_window padding is ~8 px top+bottom,
    # leaving ~64 px usable; spacer = (64 - 44) // 2 = 10 px.
    with dpg.child_window(width=-1, height=80, border=True,
                          no_scrollbar=True,
                          tag="node_palette_bar"):
        dpg.add_spacer(height=10)
        with dpg.table(header_row=False,
                       borders_innerH=False, borders_innerV=False,
                       borders_outerH=False, borders_outerV=False,
                       pad_outerX=False):
            dpg.add_table_column(init_width_or_weight=1.0, width_stretch=True)
            dpg.add_table_column(init_width_or_weight=186.0, width_fixed=True)
            with dpg.table_row():

                # ── Left cell: node-creation buttons ─────────────────────
                with dpg.table_cell():

                    # ── Main palette view ─────────────────────────────────
                    with dpg.group(horizontal=True, tag="palette_main_grp"):
                        dpg.add_spacer(width=8)
                        # Label + help button, vertically centred with buttons
                        with dpg.group(horizontal=False):
                            dpg.add_spacer(height=14)
                            dpg.add_text("Click or drag")
                        dpg.add_spacer(width=2)
                        with dpg.group(horizontal=False):
                            dpg.add_spacer(height=9)
                            dpg.add_button(label=" ? ", width=26, height=26,
                                           callback=_show_palette_help)
                        dpg.add_spacer(width=10)
                        for _pt, _plabel in [("Multiplicity", "Multiplicity"),
                                              ("Selection",    "Selection")]:
                            _pbtn = dpg.add_button(
                                label=_plabel, width=160, height=44,
                                callback=lambda s, a, u: create_node_below_lowest(u),
                                user_data=_pt,
                            )
                            with dpg.drag_payload(parent=_pbtn, drag_data=_pt,
                                                  label=f"  + {_plabel}  "):
                                pass
                            dpg.add_spacer(width=8)
                        # Observable button — click to expand submenu (not draggable)
                        dpg.add_button(label="Observable", width=160, height=44,
                                       callback=_show_obs_submenu)
                        dpg.add_spacer(width=8)
                        _pbtn = dpg.add_button(label="Histogram", width=160, height=44,
                                               callback=lambda s, a, u: create_node_below_lowest(u),
                                               user_data="Histogram")
                        with dpg.drag_payload(parent=_pbtn, drag_data="Histogram",
                                              label="  + Histogram  "):
                            pass

                    # ── Observable submenu (hidden until Observable clicked) ──
                    with dpg.group(horizontal=True, tag="palette_obs_grp",
                                   show=False):
                        dpg.add_spacer(width=8)
                        dpg.add_button(label="< Back", width=90, height=44,
                                       callback=_show_main_palette)
                        dpg.add_spacer(width=12)
                        for _pt, _pl in [("ObsGlobal",    "Global"),
                                          ("ObsObject",    "Object"),
                                          ("ObsVectorSum", "Vec Sum"),
                                          ("ObsCustom",    "Custom")]:
                            _pb = dpg.add_button(
                                label=_pl, width=130, height=44,
                                callback=lambda s, a, u: create_node_below_lowest(u),
                                user_data=_pt,
                            )
                            with dpg.drag_payload(parent=_pb, drag_data=_pt,
                                                  label=f"  + {_pl}  "):
                                pass
                            dpg.add_spacer(width=8)

                # ── Right cell: delete unconnected (right-aligned) ────────
                with dpg.table_cell():
                    dpg.add_spacer(width=8)
                    dpg.add_button(label="Delete Unconnected", width=170, height=44,
                                   tag="btn_delete_unconnected",
                                   callback=lambda: delete_unconnected_nodes())

# ── Delete Unconnected button dark-red theme ──────────────────────────────────
with dpg.theme(tag="delete_unconnected_theme"):
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button,        (139, 30, 30),
                            category=dpg.mvThemeCat_Core)
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (170, 50, 50),
                            category=dpg.mvThemeCat_Core)
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (110, 20, 20),
                            category=dpg.mvThemeCat_Core)
dpg.bind_item_theme("btn_delete_unconnected", "delete_unconnected_theme")

# ── Progress bar green theme ──────────────────────────────────────────────────
with dpg.theme(tag="progress_bar_theme"):
    with dpg.theme_component(dpg.mvProgressBar):
        dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,
                            (40, 167, 69), category=dpg.mvThemeCat_Core)
dpg.bind_item_theme("ui_progress_bar", "progress_bar_theme")
for _wi in range(8):
    dpg.bind_item_theme(f"worker_bar_{_wi}", "progress_bar_theme")

# ── Bind large font to Run button ─────────────────────────────────────────────
if _large_font is not None:
    dpg.bind_item_font("btn_trigger", _large_font)

# ── Create initial nodes ──────────────────────────────────────────────────────
_X_STEP = 310  # horizontal gap between nodes
create_node("DataSource",   pos=[30,              100], name="IDEA 91 GeV data")
create_node("Multiplicity", pos=[30 + _X_STEP,    100], name="2 leptons")
create_node("Selection",    pos=[30 + _X_STEP * 2, 100], name="di-lepton")
create_node("ObsVectorSum", pos=[30 + _X_STEP * 3, 100], name="Di-lepton mass")
create_node("Histogram",    pos=[30 + _X_STEP * 4, 100], name="Di-lepton mass")

# ── Set physics-meaningful defaults for Z-boson template ─────────────────────
# Multiplicity: require exactly 2 or more leptons
dpg.set_value("txt_leptons_1", 2)
# Selection: require at least 2 leptons with pT thresholds
dpg.set_value("txt_sel_2", "l1.pt > 20 and l2.pt > 10")
# Histogram: 50 bins over 60-120 GeV to capture the Z boson peak
dpg.set_value("txt_bins_4", 50)
dpg.set_value("txt_range_min_4", 60.0)
dpg.set_value("txt_range_max_4", 120.0)

# ── Connect initial nodes in pipeline order ───────────────────────────────────
for _out_nid, _in_nid in [(0, 1), (1, 2), (2, 3), (3, 4)]:
    try:
        _s = dpg.get_alias_id(f"slot_out_{_out_nid}")
        _e = dpg.get_alias_id(f"slot_in_{_in_nid}")
        _lid = dpg.add_node_link(_s, _e, parent="node_editor_container")
        REGISTRY.links[_lid] = (_s, _e)
        REGISTRY.connections[_s] = _e
    except Exception:
        pass

setup_link_handlers()

# ── Viewport ──────────────────────────────────────────────────────────────────
dpg.create_viewport(
    title="Future Collider Experiment",
    width=1440,
    height=920,
    resizable=True,
    small_icon=os.path.join(_HERE, "fce.ico") if os.path.exists(os.path.join(_HERE, "fce.ico")) else "",
    large_icon=os.path.join(_HERE, "fce.ico") if os.path.exists(os.path.join(_HERE, "fce.ico")) else "",
)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.set_primary_window("primary_studio_window", True)
dpg.maximize_viewport()
dpg.set_frame_callback(frame=1, callback=show_tutorial)
dpg.start_dearpygui()
dpg.destroy_context()
