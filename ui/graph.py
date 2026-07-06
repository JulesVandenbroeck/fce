import re
import dearpygui.dearpygui as dpg
from ui.state import REGISTRY, NODE_HIERARCHY, NODE_LABELS

# ---------------------------------------------------------------------------
# Variable catalogue for autocomplete
# ---------------------------------------------------------------------------
SEL_ALL_VARS = [
    "nlep", "nel", "nmu", "njets", "nphot",
    "l1.pt", "l1.eta", "l1.phi", "l1.e", "l1.d0", "l1.z0", "l1.p4",
    "l2.pt", "l2.eta", "l2.phi", "l2.e", "l2.d0", "l2.z0", "l2.p4",
    "j1.pt", "j1.eta", "j1.phi", "j1.e", "j1.btag", "j1.p4",
    "j2.pt", "j2.eta", "j2.phi", "j2.e", "j2.btag", "j2.p4",
    "ph1.pt", "ph1.eta", "ph1.phi", "ph1.e", "ph1.p4",
    "ph2.pt", "ph2.eta", "ph2.phi", "ph2.e", "ph2.p4",
    "met.pt", "met.eta", "met.phi", "met.e", "met.p4",
]

_SEL_MAX_SUGS = 5

_EXPR_TOOLTIP = (
    "Variables  (objects pt-sorted within type)\n"
    "  Counts  :  nlep  nel  nmu  njets  nphot\n"
    "  Leptons :  l1.pt  l1.eta  l1.phi  l1.e  l1.d0  l1.z0  l1.p4\n"
    "             l2.pt  l2.eta  l2.phi  l2.e  l2.d0  l2.z0  l2.p4\n"
    "  Jets    :  j1.pt  j1.eta  j1.phi  j1.e  j1.btag  j1.p4\n"
    "             j2.pt  j2.eta  j2.phi  j2.e  j2.btag  j2.p4\n"
    "  Photons :  ph1.pt  ph1.eta  ph1.phi  ph1.e  ph1.p4\n"
    "             ph2.pt  ph2.eta  ph2.phi  ph2.e  ph2.p4\n"
    "  MET     :  met.pt  met.eta  met.phi  met.e  met.p4\n\n"
    "4-vector arithmetic (p4 objects)\n"
    "  (l1.p4 + l2.p4).mass   → invariant mass\n"
    "  (l1.p4 + l2.p4).pt     → system pT\n"
    "  l1.p4.deltaR(l2.p4)    → ΔR\n"
    "  deltaR(l1, l2)          → ΔR via eta/phi\n\n"
    "Operators  :  > < >= <= == !=\n"
    "Logic      :  and  or  not  ( )"
)

# ---------------------------------------------------------------------------
# Fit signal choices per energy (must match samples.json keys minus 'data')
# ---------------------------------------------------------------------------
_FIT_CHOICES = {
    "91":  ["None", "New Physics", "X1", "X2", "X3", "X4", "X5"],
    "160": ["None", "New Physics", "X1", "X2", "X3", "X4"],
    "240": ["None", "New Physics", "X1", "X2", "X3"],
    "365": ["None", "New Physics", "X1", "X2", "X3", "X4", "X5"],
}


# ---------------------------------------------------------------------------
# Slot ↔ node-id resolution
# ---------------------------------------------------------------------------

def _register_slot(tag: str, nid: int):
    REGISTRY.slot_node[tag] = nid
    try:
        uuid = dpg.get_alias_id(tag)
        REGISTRY.slot_node[uuid] = nid
    except Exception:
        pass


def _nid_from_slot(slot_id) -> int | None:
    nid = REGISTRY.slot_node.get(slot_id)
    if nid is not None:
        return nid
    try:
        alias = dpg.get_item_alias(slot_id)
        if alias:
            for prefix in ("slot_out_", "slot_in_"):
                if alias.startswith(prefix):
                    return int(alias[len(prefix):])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Link callbacks
# ---------------------------------------------------------------------------

_CHAINABLE_TYPES = {"Multiplicity", "Selection"}


def _normalize_slot(slot_id):
    """Normalize a slot identifier to its integer alias id.

    The node editor's link callback may hand back either the string tag
    or the integer alias id for an attribute depending on how the link
    was created. Connections must be stored under one consistent type or
    later membership checks (which always compare integer ids) silently
    fail and every node looks disconnected.
    """
    if isinstance(slot_id, str):
        try:
            return dpg.get_alias_id(slot_id)
        except Exception:
            return slot_id
    return slot_id


def link_callback(sender, app_data):
    start_slot = _normalize_slot(app_data[0])
    end_slot   = _normalize_slot(app_data[1])
    start_nid = _nid_from_slot(start_slot)
    end_nid   = _nid_from_slot(end_slot)
    if start_nid is not None and end_nid is not None:
        src_type  = REGISTRY.nodes.get(start_nid)
        dst_type  = REGISTRY.nodes.get(end_nid)
        src_level = NODE_HIERARCHY.get(src_type, -1)
        dst_level = NODE_HIERARCHY.get(dst_type, 99)
        # Allow same-type chaining for Multiplicity and Selection (AND logic)
        same_type_chain = src_type == dst_type and src_type in _CHAINABLE_TYPES
        if not same_type_chain and src_level >= dst_level:
            return
    link_id = dpg.add_node_link(start_slot, end_slot, parent=sender)
    REGISTRY.links[link_id] = (start_slot, end_slot)
    REGISTRY.connections[start_slot] = end_slot


def delink_callback(sender, app_data):
    link_id = app_data
    if link_id in REGISTRY.links:
        start, _ = REGISTRY.links.pop(link_id)
        REGISTRY.connections.pop(start, None)
    if dpg.does_item_exist(link_id):
        dpg.delete_item(link_id)


def _on_right_click_link(sender=None, app_data=None, user_data=None):
    """Delete a link when it is right-clicked in the node editor."""
    if not dpg.does_item_exist("node_editor_container"):
        return
    if not dpg.is_item_hovered("node_editor_container"):
        return
    for link_id in list(REGISTRY.links.keys()):
        if dpg.does_item_exist(link_id) and dpg.is_item_hovered(link_id):
            delink_callback(None, link_id)
            break


_PAN_SPEED = 20  # pixels per scroll notch


def _on_wheel_pan(sender=None, app_data=None, user_data=None):
    """Pan the node editor canvas with the mouse wheel."""
    if not dpg.does_item_exist("node_editor_container"):
        return
    if not dpg.is_item_hovered("node_editor_container"):
        return
    delta = app_data  # +1 = scroll up, -1 = scroll down
    shift = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)
    dx = int(delta * _PAN_SPEED) if shift else 0
    dy = int(delta * _PAN_SPEED) if not shift else 0
    for nid in list(REGISTRY.nodes.keys()):
        node_tag = f"node_{nid}"
        if dpg.does_item_exist(node_tag):
            x, y = dpg.get_item_pos(node_tag)
            dpg.set_item_pos(node_tag, [x + dx, y + dy])


def setup_link_handlers():
    with dpg.handler_registry():
        dpg.add_mouse_click_handler(
            button=dpg.mvMouseButton_Right,
            callback=_on_right_click_link,
        )
        dpg.add_mouse_wheel_handler(callback=_on_wheel_pan)


# ---------------------------------------------------------------------------
# Node error highlighting (only called from Run, never from keystroke callbacks)
# ---------------------------------------------------------------------------

_NODE_ERRORS: dict[int, str] = {}  # nid -> error message


def _set_node_error(nid: int, has_error: bool, msg: str = ""):
    node_tag  = f"node_{nid}"
    theme_tag = f"node_err_theme_{nid}"
    bang_tag  = f"btn_bang_{nid}"
    if not dpg.does_item_exist(node_tag):
        return
    if dpg.does_item_exist(theme_tag):
        dpg.delete_item(theme_tag)
    if has_error:
        _NODE_ERRORS[nid] = msg
        with dpg.theme(tag=theme_tag):
            with dpg.theme_component(dpg.mvNode):
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackground,
                                    (70, 15, 15), category=dpg.mvThemeCat_Nodes)
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundHovered,
                                    (90, 25, 25), category=dpg.mvThemeCat_Nodes)
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected,
                                    (90, 25, 25), category=dpg.mvThemeCat_Nodes)
                dpg.add_theme_color(dpg.mvNodeCol_NodeOutline,
                                    (200, 50, 50), category=dpg.mvThemeCat_Nodes)
        dpg.bind_item_theme(node_tag, theme_tag)
        if dpg.does_item_exist(bang_tag):
            dpg.configure_item(bang_tag, show=True)
    else:
        _NODE_ERRORS.pop(nid, None)
        dpg.bind_item_theme(node_tag, 0)
        if dpg.does_item_exist(bang_tag):
            dpg.configure_item(bang_tag, show=False)


def _show_node_error(sender, app_data, user_data):
    nid = user_data
    msg = _NODE_ERRORS.get(nid, "No details available.")
    if dpg.does_item_exist("node_error_window"):
        dpg.set_value("node_error_text", msg)
        vp_w = dpg.get_viewport_width()
        vp_h = dpg.get_viewport_height()
        dpg.set_item_pos("node_error_window", [(vp_w - 440) // 2, (vp_h - 180) // 2])
        dpg.configure_item("node_error_window", show=True)
        dpg.focus_item("node_error_window")


def clear_all_node_errors():
    for nid in list(REGISTRY.nodes.keys()):
        _set_node_error(nid, False)


def validate_node_expressions() -> list[tuple[int, str]]:
    """Check syntax of all expression fields. Returns list of (nid, error_msg)."""
    errors = []
    for nid, ntype in REGISTRY.nodes.items():
        if ntype == "Selection":
            field = f"txt_sel_{nid}"
        elif ntype == "Observable":
            field = f"txt_obs_{nid}"
        else:
            continue
        if not dpg.does_item_exist(field):
            continue
        expr = dpg.get_value(field).strip()
        if not expr:
            errors.append((nid, "Expression is empty."))
            continue
        try:
            compile(expr, "<expr>", "eval")
        except SyntaxError as e:
            errors.append((nid, f"Syntax error: {e.msg}\n  {expr}"))
    return errors


def mark_nodes_from_pipeline_check(error_nids: list[int], all_nids: list[int]):
    connected_starts = set(REGISTRY.connections.keys())
    connected_ends   = set(REGISTRY.connections.values())
    for nid in all_nids:
        if nid in error_nids:
            ntype = REGISTRY.nodes.get(nid, "")
            parts = []
            if ntype != "Histogram":
                sid = _slot_id(f"slot_out_{nid}")
                if sid is None or sid not in connected_starts:
                    parts.append("output not connected")
            if ntype != "DataSource":
                sid = _slot_id(f"slot_in_{nid}")
                if sid is None or sid not in connected_ends:
                    parts.append("input not connected")
            _set_node_error(nid, True, "Pipeline: " + (", ".join(parts) or "not connected"))
        else:
            _set_node_error(nid, False)


# ---------------------------------------------------------------------------
# Expression-field helpers
# ---------------------------------------------------------------------------

def _get_suggestions(token: str) -> list:
    if len(token) < 1:
        return []
    return [v for v in SEL_ALL_VARS if v.startswith(token) and v != token][:_SEL_MAX_SUGS]


def _apply_suggestion(nid: int, suggestion: str):
    for field in (f"txt_sel_{nid}", f"txt_obs_{nid}"):
        if dpg.does_item_exist(field):
            text     = dpg.get_value(field)
            new_text = re.sub(r"[\w.]+$", suggestion, text)
            dpg.set_value(field, new_text)
            _on_expr_change(new_text, nid)
            return


def _on_expr_change(text: str, nid: int):
    """Update autocomplete suggestions only — no validation during typing."""
    m     = re.search(r"[\w.]+$", text)
    token = m.group() if m else ""
    sugs  = _get_suggestions(token)

    for si in range(_SEL_MAX_SUGS):
        btn = f"sel_sug_{nid}_{si}"
        if not dpg.does_item_exist(btn):
            continue
        if si < len(sugs):
            dpg.configure_item(btn, label=sugs[si], show=True, user_data=(nid, sugs[si]))
        else:
            dpg.configure_item(btn, show=False)


def _make_expr_widgets(tag: str, default: str, hint: str,
                       nid: int, parent_tag: str, width: int = 222):
    dpg.add_input_text(
        tag=tag, default_value=default, width=width, hint=hint,
        callback=lambda s, a, u: _on_expr_change(a, u),
        user_data=nid, parent=parent_tag,
    )

    sug_grp = f"sug_grp_{nid}"
    dpg.add_group(horizontal=True, tag=sug_grp, parent=parent_tag)
    for si in range(_SEL_MAX_SUGS):
        dpg.add_button(
            label="", tag=f"sel_sug_{nid}_{si}", small=True, show=False,
            callback=lambda s, a, u: _apply_suggestion(*u),
            user_data=(nid, ""), parent=sug_grp,
        )
    _on_expr_change(default, nid)


# ---------------------------------------------------------------------------
# Energy change → update Histogram fit-signal combo
# ---------------------------------------------------------------------------

def _on_energy_change(energy_val: str, _ds_nid: int):
    en = energy_val.replace(" GeV", "")
    choices = _FIT_CHOICES.get(en, ["None"])
    for nid, node_type in REGISTRY.nodes.items():
        if node_type == "Histogram" and dpg.does_item_exist(f"cb_target_{nid}"):
            current = dpg.get_value(f"cb_target_{nid}")
            dpg.configure_item(f"cb_target_{nid}", items=choices)
            if current not in choices:
                dpg.set_value(f"cb_target_{nid}", "None")


# ---------------------------------------------------------------------------
# Node deletion
# ---------------------------------------------------------------------------

def delete_node(nid: int):
    node_slots = {f"slot_in_{nid}", f"slot_out_{nid}"}
    for k, v in list(REGISTRY.slot_node.items()):
        if v == nid:
            node_slots.add(k)

    dead = [lid for lid, (s, e) in REGISTRY.links.items()
            if s in node_slots or e in node_slots]
    for lid in dead:
        start, _ = REGISTRY.links.pop(lid)
        REGISTRY.connections.pop(start, None)

    for k in list(REGISTRY.slot_node):
        if REGISTRY.slot_node[k] == nid:
            del REGISTRY.slot_node[k]

    REGISTRY.nodes.pop(nid, None)

    node_tag = f"node_{nid}"
    if dpg.does_item_exist(node_tag):
        dpg.delete_item(node_tag)


# ---------------------------------------------------------------------------
# Dynamic node creation
# ---------------------------------------------------------------------------

def _add_node_widgets(node_type: str, nid: int, parent_tag: str):
    if node_type == "DataSource":
        dpg.add_combo(
            ["91 GeV", "160 GeV", "240 GeV", "365 GeV"],
            label="Energy", tag=f"cb_energy_{nid}",
            default_value="91 GeV", width=110, parent=parent_tag,
            callback=lambda s, a, u: _on_energy_change(a, u),
            user_data=nid,
        )
        dpg.add_combo(
            ["IDEA", "CLD"],
            label="Detector", tag=f"cb_detector_{nid}",
            default_value="IDEA", width=110, parent=parent_tag,
        )

    elif node_type == "Multiplicity":
        dpg.add_combo(
            ["Any", "Electron", "Muon"],
            label="Lepton", tag=f"cb_ltype_{nid}",
            default_value="Any", width=90, parent=parent_tag,
        )
        dpg.add_input_int(
            label="Min Leptons", tag=f"txt_leptons_{nid}",
            default_value=0, width=90, parent=parent_tag,
        )
        dpg.add_input_int(
            label="Min Jets", tag=f"txt_jets_{nid}",
            default_value=0, width=90, parent=parent_tag,
        )
        dpg.add_input_int(
            label="Min Photons", tag=f"txt_photons_{nid}",
            default_value=0, width=90, parent=parent_tag,
        )

    elif node_type == "Selection":
        _make_expr_widgets(
            tag=f"txt_sel_{nid}",
            default="nlep >= 2",
            hint="e.g.  (l1.p4+l2.p4).mass > 70",
            nid=nid, parent_tag=parent_tag, width=222,
        )

    elif node_type == "Observable":
        _make_expr_widgets(
            tag=f"txt_obs_{nid}",
            default="met.pt",
            hint="e.g.  (l1.p4+l2.p4).mass",
            nid=nid, parent_tag=parent_tag, width=200,
        )

    elif node_type == "Histogram":
        # Initial choices for default energy 91 GeV
        dpg.add_combo(
            _FIT_CHOICES.get("91", ["None"]),
            label="Fit Signal", tag=f"cb_target_{nid}",
            default_value="None", width=110, parent=parent_tag,
        )
        dpg.add_input_int(
            label="Bins", tag=f"txt_bins_{nid}",
            default_value=40, width=90, parent=parent_tag,
        )
        dpg.add_input_float(
            label="Min Range", tag=f"txt_range_min_{nid}",
            default_value=0.0, width=90, parent=parent_tag,
        )
        dpg.add_input_float(
            label="Max Range", tag=f"txt_range_max_{nid}",
            default_value=150.0, width=90, parent=parent_tag,
        )


# ---------------------------------------------------------------------------
# Help window show
# ---------------------------------------------------------------------------

def _show_help_window(sender=None, app_data=None, user_data=None):
    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    w, h = 520, 420
    dpg.set_item_pos("help_expr_window", [(vp_w - w) // 2, (vp_h - h) // 2])
    dpg.configure_item("help_expr_window", show=True)
    dpg.focus_item("help_expr_window")


# Spacer width to align × (and ? for expr nodes) to the right edge
_CLOSE_SPACER = {
    "DataSource":   162,
    "Multiplicity": 122,
    "Selection":    180,   # reduced: ? button takes ~25px
    "Observable":   160,   # reduced: ? button takes ~25px
    "Histogram":    158,
}

_HAS_HELP = {"Selection", "Observable"}


def create_node(node_type: str, pos: list | None = None):
    # Prevent more than one DataSource
    if node_type == "DataSource":
        if any(t == "DataSource" for t in REGISTRY.nodes.values()):
            from ui.components import log_to_message_center
            log_to_message_center("Data node already exists.")
            return None

    nid = REGISTRY.next_id
    REGISTRY.next_id += 1
    REGISTRY.nodes[nid] = node_type

    if pos is None:
        count = sum(1 for t in REGISTRY.nodes.values() if t == node_type) - 1
        pos = [100 + count * 35, 100 + count * 35]

    node_tag = f"node_{nid}"
    dpg.add_node(
        label=NODE_LABELS.get(node_type, node_type),
        tag=node_tag,
        parent="node_editor_container",
        pos=pos,
    )

    # ── Close (×) row — with optional ? button for expr nodes ────────────
    close_attr = f"slot_close_{nid}"
    dpg.add_node_attribute(
        attribute_type=dpg.mvNode_Attr_Static,
        tag=close_attr, parent=node_tag,
    )
    close_grp = f"grp_close_{nid}"
    dpg.add_group(horizontal=True, tag=close_grp, parent=close_attr)
    dpg.add_spacer(width=_CLOSE_SPACER.get(node_type, 150), parent=close_grp)

    if node_type in _HAS_HELP:
        dpg.add_button(
            label="!", tag=f"btn_bang_{nid}", small=True, show=False,
            callback=_show_node_error,
            user_data=nid, parent=close_grp,
        )
        dpg.add_button(
            label="?", tag=f"btn_help_{nid}", small=True,
            callback=_show_help_window,
            parent=close_grp,
        )

    dpg.add_button(
        label="×", tag=f"btn_close_{nid}", small=True,
        callback=lambda s, a, u: delete_node(u),
        user_data=nid, parent=close_grp,
    )

    # ── Input slot ───────────────────────────────────────────────────────
    if node_type != "DataSource":
        in_tag = f"slot_in_{nid}"
        dpg.add_node_attribute(
            attribute_type=dpg.mvNode_Attr_Input,
            tag=in_tag, parent=node_tag,
        )
        dpg.add_spacer(width=4, parent=in_tag)
        _register_slot(in_tag, nid)

    # ── Output slot (or static for Histogram) ────────────────────────────
    if node_type != "Histogram":
        out_tag = f"slot_out_{nid}"
        dpg.add_node_attribute(
            attribute_type=dpg.mvNode_Attr_Output,
            tag=out_tag, parent=node_tag,
        )
        _add_node_widgets(node_type, nid, out_tag)
        _register_slot(out_tag, nid)
    else:
        in_tag = f"slot_in_{nid}"
        _add_node_widgets(node_type, nid, in_tag)

    return nid


# ---------------------------------------------------------------------------
# Graph topology compiler
# ---------------------------------------------------------------------------

def compile_graph_topology() -> dict:
    nodes = REGISTRY.nodes

    ds_nids   = [n for n, t in nodes.items() if t == "DataSource"]
    mul_nids  = [n for n, t in nodes.items() if t == "Multiplicity"]
    sel_nids  = [n for n, t in nodes.items() if t == "Selection"]
    obs_nids  = [n for n, t in nodes.items() if t == "Observable"]
    hist_nids = [n for n, t in nodes.items() if t == "Histogram"]

    ds = ds_nids[0] if ds_nids else None
    energy   = dpg.get_value(f"cb_energy_{ds}")   if ds is not None else "91 GeV"
    detector = dpg.get_value(f"cb_detector_{ds}") if ds is not None else "IDEA"

    mult_cuts = []
    for n in mul_nids:
        nlep  = int(dpg.get_value(f"txt_leptons_{n}"))
        njets = int(dpg.get_value(f"txt_jets_{n}"))
        ltype = dpg.get_value(f"cb_ltype_{n}") if dpg.does_item_exist(f"cb_ltype_{n}") else "Any"
        nphot = int(dpg.get_value(f"txt_photons_{n}")) if dpg.does_item_exist(f"txt_photons_{n}") else 0
        mult_cuts.append((nlep, njets, ltype, nphot))

    sel_exprs = [
        dpg.get_value(f"txt_sel_{n}").strip()
        for n in sel_nids
        if dpg.does_item_exist(f"txt_sel_{n}") and dpg.get_value(f"txt_sel_{n}").strip()
    ]

    obs  = obs_nids[0]  if obs_nids  else None
    hist = hist_nids[0] if hist_nids else None

    observable = dpg.get_value(f"txt_obs_{obs}").strip() if obs is not None else "met.pt"
    target     = dpg.get_value(f"cb_target_{hist}")          if hist is not None else "None"
    bins       = str(dpg.get_value(f"txt_bins_{hist}"))      if hist is not None else "40"
    rng_min    = str(dpg.get_value(f"txt_range_min_{hist}")) if hist is not None else "0.0"
    rng_max    = str(dpg.get_value(f"txt_range_max_{hist}")) if hist is not None else "150.0"

    import hashlib
    # selection-level hash: changes only when data source or cuts change
    h5_sel = hashlib.md5(
        (energy + detector + str(mult_cuts) + str(sel_exprs)).encode()
    ).hexdigest()
    # full hash: changes when anything (including observable / bins) changes
    h5 = hashlib.md5(
        (h5_sel + observable + bins + rng_min + rng_max + target).encode()
    ).hexdigest()

    return {
        "energy": energy, "detector": detector,
        "observable": observable,
        "bins": bins, "min": rng_min, "max": rng_max,
        "target": target, "h5": h5, "h5_sel": h5_sel,
        "mult_cuts": mult_cuts, "sel_exprs": sel_exprs,
    }


# ---------------------------------------------------------------------------
# Pipeline connectivity check — returns list of error node ids
# ---------------------------------------------------------------------------

def _slot_id(tag: str):
    """Return the integer DPG ID for a slot tag (what link_callback stores)."""
    try:
        return dpg.get_alias_id(tag)
    except Exception:
        return None


def check_pipeline_connectivity() -> list[int]:
    """Return node ids that are not properly connected in the pipeline."""
    connected_starts = set(REGISTRY.connections.keys())
    connected_ends   = set(REGISTRY.connections.values())
    error_nids = []

    for nid, ntype in REGISTRY.nodes.items():
        if ntype != "Histogram":
            sid = _slot_id(f"slot_out_{nid}")
            if sid is None or sid not in connected_starts:
                error_nids.append(nid)

        if ntype != "DataSource":
            sid = _slot_id(f"slot_in_{nid}")
            if sid is None or sid not in connected_ends:
                error_nids.append(nid)

    return list(set(error_nids))
