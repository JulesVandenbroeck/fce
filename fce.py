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

from ui.graph import link_callback, delink_callback, create_node, setup_link_handlers, on_node_editor_drop
from ui.state import REGISTRY
from ui.components import trigger_analysis_pipeline, trigger_dataset_download, confirm_redownload, MAX_HIST_TEXTURES
import ui.state as _ui_state
from fce_studio import __version__

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Clear caches from previous sessions ───────────────────────────────────────
# Best-effort: a stale/unwritable cache must never block startup.
try:
    _FCE_DIR = get_fce_home()
    for _cache_subdir in ("cache", "output"):
        _d = os.path.join(_FCE_DIR, _cache_subdir)
        try:
            if os.path.exists(_d):
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


# ── Window show helpers ───────────────────────────────────────────────────────

def _show_about_window(sender=None, app_data=None, user_data=None):
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos("about_window", [(vp_w - 360) // 2, (vp_h - 280) // 2])
    dpg.configure_item("about_window", show=True)
    dpg.focus_item("about_window")


# ── Main window ───────────────────────────────────────────────────────────────
with dpg.window(tag="primary_studio_window", label="Future Collider Experiment"):

    with dpg.viewport_menu_bar():

        with dpg.menu(label="File"):
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
            dpg.add_menu_item(
                label="Observable",
                callback=lambda: create_node("Observable"),
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

    # ── Layout: node editor (left) + control panel with console (right) ───
    with dpg.group(horizontal=True):

        # Left: node editor (leaves room for palette at bottom)
        with dpg.child_window(width=-670, height=-95, border=False,
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
        with dpg.child_window(width=660, height=-95, border=False):

            dpg.add_spacer(height=22)
            dpg.add_progress_bar(
                label="Progress",
                tag="ui_progress_bar",
                default_value=0.0,
                overlay="Ready",
                width=-1,
                height=22,
            )
            dpg.add_spacer(height=5)
            dpg.add_button(
                label="Run",
                tag="btn_trigger",
                callback=trigger_analysis_pipeline,
                width=-1,
                height=42,
            )
            dpg.add_spacer(height=5)

            with dpg.collapsing_header(
                label="Statistical fit",
                tag="stat_fit_header",
                default_open=False,
            ):
                dpg.add_text("Best Fit Parameter: N/A",      tag="ui_txt_mu")
                dpg.add_text("Discovery Significance: N/A",  tag="ui_txt_sig")

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
    with dpg.child_window(width=-1, height=90, border=True,
                          tag="node_palette_bar"):
        dpg.add_text("Drag to canvas ›", tag="palette_drag_text", indent=0)
        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True, tag="palette_btn_group"):
            for _pt, _plabel in [
                ("Multiplicity", "Multiplicity"),
                ("Selection",    "Selection"),
                ("Observable",   "Observable"),
                ("Histogram",    "Histogram"),
            ]:
                _pbtn = dpg.add_button(label=_plabel, width=160, height=44)
                with dpg.drag_payload(parent=_pbtn, drag_data=_pt,
                                      label=f"  + {_plabel}  "):
                    pass
                dpg.add_spacer(width=8)

# ── Bind large font to Run button ─────────────────────────────────────────────
if _large_font is not None:
    dpg.bind_item_font("btn_trigger", _large_font)

# ── Create initial nodes ──────────────────────────────────────────────────────
_X_STEP = 310  # horizontal gap between nodes
create_node("DataSource",   pos=[30,              100], name="IDEA 91 GeV data")
create_node("Multiplicity", pos=[30 + _X_STEP,    100], name="all events")
create_node("Selection",    pos=[30 + _X_STEP * 2, 100], name="2 leptons")
create_node("Observable",   pos=[30 + _X_STEP * 3, 100], name="MET pT")
create_node("Histogram",    pos=[30 + _X_STEP * 4, 100], name="MET pT")

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


def _center_palette():
    """Center palette text and button group once the viewport width is known."""
    vp_w = dpg.get_viewport_width()
    # "Drag to canvas ›" ≈ 120 px; buttons 4×160 + 4×8 spacers ≈ 672 px
    dpg.configure_item("palette_drag_text", indent=max(0, (vp_w - 120) // 2))
    dpg.configure_item("palette_btn_group", indent=max(0, (vp_w - 672) // 2))


dpg.set_frame_callback(frame=1, callback=_center_palette)

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
dpg.start_dearpygui()
dpg.destroy_context()
