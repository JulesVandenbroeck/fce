"""Launch tutorial shown on application startup."""
import dearpygui.dearpygui as dpg
import ui.state as _state

_WIN_TAG = "tutorial_window"
_WIN_W = 640
_WIN_H = 310

_PAGE = [0]

# (title, body_text, highlight_tag_or_None, highlight_type)
# highlight_type: "node" | "item" | "window" | None
# All text uses ASCII only: DPG's default glyph range is Latin-1 (0x00-0xFF),
# so characters above U+00FF (bullets, arrows, Greek, em-dash) render as "?".
_PAGES = [
    (
        "Welcome to FCE Studio",
        "Welcome to the Future Collider Experiment (FCE) Studio!\n\n"
        "FCE Studio is a learning tool for particle physics data analysis\n"
        "at a future lepton collider. Build an analysis pipeline by connecting\n"
        "nodes on the canvas, then press Run to process simulated collision\n"
        "data and produce histograms.\n\n"
        "Use Next to explore each feature, or Skip to dismiss this tutorial.",
        None, None,
    ),
    (
        "Data Node",
        "The Data node is the starting point of every analysis pipeline.\n\n"
        "  -  Energy: centre-of-mass energy (91 / 160 / 240 / 365 GeV)\n"
        "  -  Detector: detector geometry (IDEA or CLD)\n\n"
        "Exactly one Data node is allowed per pipeline. The selected energy\n"
        "determines which signal samples are available for statistical fitting.",
        "node_0", "node",
    ),
    (
        "Multiplicity Node",
        "The Multiplicity node filters events by minimum object counts.\n\n"
        "  -  Lepton type: Any, Electron, or Muon\n"
        "  -  Min Leptons / Jets / Photons: minimum required count\n\n"
        "Multiple Multiplicity nodes can be chained - all conditions are\n"
        "combined with AND logic. Setting all counts to 0 accepts every event.",
        "node_1", "node",
    ),
    (
        "Selection Node",
        "The Selection node filters events with a boolean expression.\n\n"
        "Examples:\n"
        "  nlep >= 2                      at least 2 leptons\n"
        "  l1.pt > 20 and l2.pt > 10\n"
        "  (l1.p4 + l2.p4).mass > 80     di-lepton mass cut\n\n"
        "Press '?' inside the node for the full variable reference. Multiple\n"
        "Selection nodes can be chained (AND logic).",
        "node_2", "node",
    ),
    (
        "Observable Node",
        "The Observable node defines the quantity plotted on the x-axis.\n\n"
        "Four types are available from the palette Observable button:\n\n"
        "  Global  - event counts: nlep, nel, nmu, njets, nphot\n"
        "  Object  - object property: choose object (l1, j1, met, ...)\n"
        "            and variable (pt, eta, phi, e; btag for jets;\n"
        "            d0, z0 for leptons). Use '+' to sum multiple terms.\n"
        "  Vec Sum - combined system quantity, e.g. di-lepton mass:\n"
        "            pick the result (mass, pt, ...) and two or more objects.\n"
        "  Custom  - free-text expression for advanced users.\n\n"
        "Multiple Observable nodes can share one Selection node.",
        "node_3", "node",
    ),
    (
        "Histogram Node",
        "The Histogram node controls how the observable is plotted.\n\n"
        "  -  Bins: number of histogram bins\n"
        "  -  Min / Max Range: the x-axis range of the histogram\n"
        "  -  Fit Signal: optional signal sample for a statistical fit\n"
        "     (returns signal strength mu and discovery significance Z)\n\n"
        "Multiple Histogram nodes can share one Observable, each producing\n"
        "a separate plot labelled by the node's custom name.",
        "node_4", "node",
    ),
    (
        "Connecting Nodes",
        "Connect nodes by dragging from an output pin to an input pin.\n\n"
        "Pipeline order:  Data -> Multiplicity -> Selection -> Observable -> Histogram\n\n"
        "  -  Create a link: drag from the right-hand pin of one node to\n"
        "     the left-hand pin of the next.\n"
        "  -  Delete a link: select it and press Delete / Backspace, or\n"
        "     right-click the link to remove it immediately.\n"
        "  -  Undo: Ctrl Z restores the last node or link deletion (up to 10).",
        None, None,
    ),
    (
        "Node Palette",
        "The palette at the bottom lets you drag new nodes onto the canvas.\n\n"
        "Multiplicity, Selection, and Histogram are dragged directly.\n\n"
        "Observable opens a submenu: click the Observable button to reveal\n"
        "four sub-types (Global, Object, Vec Sum, Custom). Drag the desired\n"
        "type onto the canvas, then click '< Back' to return to the palette.\n\n"
        "Nodes can also be added via Add Node in the top menu bar.",
        "node_palette_bar", "window",
    ),
    (
        "Canvas Navigation",
        "Navigate and organise the canvas with these controls:\n\n"
        "  -  Right-click + drag: pan all nodes\n"
        "  -  Scroll wheel: pan vertically\n"
        "  -  Shift + Scroll: pan horizontally\n"
        "  -  Drag a node header: move a single node\n"
        "  -  Select a node or link, then Delete / Backspace: remove it\n"
        "  -  x button on a node: delete that node",
        None, None,
    ),
    (
        "Running the Analysis",
        "When your pipeline is fully connected, press Run to start.\n\n"
        "FCE Studio reads the ROOT data files, applies your selection cuts,\n"
        "and fills histograms for all configured samples. A progress bar\n"
        "tracks execution. Results are cached - re-running with the same\n"
        "settings is significantly faster.\n\n"
        "If a node has a configuration error it is highlighted in red;\n"
        "click the '!' button on the node to see the error details.",
        "btn_trigger", "item",
    ),
    (
        "Results & Statistical Fit",
        "After the analysis completes, histograms appear on the right.\n\n"
        "  -  Single Histogram node: the plot fills the display area.\n"
        "  -  Multiple Histogram nodes: each plot appears in a collapsing\n"
        "     section labelled by the node's custom name.\n\n"
        "If a Fit Signal is selected, FCE Studio runs a pyhf-based fit and\n"
        "reports signal strength mu and discovery significance Z in the\n"
        "'Statistical fit' panel above the histogram.",
        "stat_fit_header", "item",
    ),
]

_PREV_HIGHLIGHT: list = [None]
_THEMES_BUILT = [False]


def _build_themes():
    if _THEMES_BUILT[0]:
        return
    _THEMES_BUILT[0] = True

    with dpg.theme(tag="_tut_node_hl"):
        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeOutline, (255, 200, 0, 255),
                category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackground, (60, 48, 5, 220),
                category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundHovered, (75, 60, 8, 220),
                category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundSelected, (90, 72, 10, 220),
                category=dpg.mvThemeCat_Nodes)

    with dpg.theme(tag="_tut_item_hl"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 200, 0, 255))
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 200, 0, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (40, 32, 5, 80))
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 3.0)
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Border,        (255, 200, 0, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (120, 95, 0, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (160, 128, 0, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (180, 145, 0, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2.0)
        with dpg.theme_component(dpg.mvCollapsingHeader):
            dpg.add_theme_color(dpg.mvThemeCol_Header,        (100, 80, 0, 200))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (130, 105, 0, 220))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,  (150, 120, 0, 220))
            dpg.add_theme_color(dpg.mvThemeCol_Border,        (255, 200, 0, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2.0)


def _clear_highlight():
    tag = _PREV_HIGHLIGHT[0]
    if tag and dpg.does_item_exist(tag):
        dpg.bind_item_theme(tag, 0)
    _PREV_HIGHLIGHT[0] = None


def _apply_highlight(tag: str, h_type: str):
    _clear_highlight()
    if not tag or not dpg.does_item_exist(tag):
        return
    theme = "_tut_node_hl" if h_type == "node" else "_tut_item_hl"
    dpg.bind_item_theme(tag, theme)
    _PREV_HIGHLIGHT[0] = tag


def _refresh():
    idx = _PAGE[0]
    total = len(_PAGES)
    title, body, h_tag, h_type = _PAGES[idx]

    dpg.set_value("_tut_title", title)
    dpg.set_value("_tut_body", body)
    dpg.set_value("_tut_pager", f"{idx + 1} / {total}")
    dpg.configure_item("_tut_prev", enabled=(idx > 0))
    last = idx == total - 1
    dpg.configure_item("_tut_next", label="Finish" if last else "Next >")

    if h_tag:
        _apply_highlight(h_tag, h_type)
    else:
        _clear_highlight()


def _on_next(s=None, a=None, u=None):
    if _PAGE[0] < len(_PAGES) - 1:
        _PAGE[0] += 1
        _refresh()
    else:
        _on_skip()


def _on_prev(s=None, a=None, u=None):
    if _PAGE[0] > 0:
        _PAGE[0] -= 1
        _refresh()


def _on_skip(s=None, a=None, u=None):
    _clear_highlight()
    _PAGE[0] = 0
    if dpg.does_item_exist(_WIN_TAG):
        dpg.configure_item(_WIN_TAG, show=False)


def _build_window():
    # modal=False so the interface is not dimmed while items are highlighted
    with dpg.window(
        tag=_WIN_TAG,
        label="FCE Studio - Tutorial",
        modal=False, show=False,
        width=_WIN_W, height=_WIN_H,
        no_resize=True, no_collapse=True,
        no_scrollbar=True,
        on_close=_on_skip,
    ):
        # Content area fills all height except the bottom nav bar (~40 px).
        # height=-40 means "leave 40 px at the bottom of the parent's content
        # area", so the nav row is always visible regardless of text length.
        with dpg.child_window(width=-1, height=-40, border=False):
            dpg.add_text("", tag="_tut_title", color=(255, 200, 50, 255))
            if _state.LARGE_FONT is not None:
                dpg.bind_item_font("_tut_title", _state.LARGE_FONT)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text("", tag="_tut_body", wrap=0)
        # Nav bar — always at the bottom
        dpg.add_separator()
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_button(label="< Prev", tag="_tut_prev",
                           callback=_on_prev, width=90)
            dpg.add_spacer(width=8)
            dpg.add_button(label="Skip", tag="_tut_skip",
                           callback=_on_skip, width=90)
            dpg.add_spacer(width=102)
            dpg.add_text("1 / 11", tag="_tut_pager")
            dpg.add_spacer(width=102)
            dpg.add_button(label="Next >", tag="_tut_next",
                           callback=_on_next, width=90)


def show_tutorial():
    """Create (once) and display the tutorial popup."""
    _build_themes()
    if not dpg.does_item_exist(_WIN_TAG):
        _build_window()
    _PAGE[0] = 0
    _refresh()
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    dpg.set_item_pos(_WIN_TAG, [max(0, (vp_w - _WIN_W) // 2),
                                max(0, (vp_h - _WIN_H) // 2)])
    dpg.configure_item(_WIN_TAG, show=True)
    dpg.focus_item(_WIN_TAG)
