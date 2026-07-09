import re
from collections import deque
import dearpygui.dearpygui as dpg
import ui.state as _state
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

# ---------------------------------------------------------------------------
# Typed Observable node catalogues
# ---------------------------------------------------------------------------

_OBS_TYPES = {"ObsGlobal", "ObsObject", "ObsVectorSum", "ObsCustom", "Observable"}


def _is_obs(ntype: str) -> bool:
    return ntype in _OBS_TYPES


_GLOBAL_VARS = ["nlep", "nel", "nmu", "njets", "nphot"]

_OBJ_VARS = {
    "l1":  ["pt", "eta", "phi", "e", "d0", "z0"],
    "l2":  ["pt", "eta", "phi", "e", "d0", "z0"],
    "j1":  ["pt", "eta", "phi", "e", "btag"],
    "j2":  ["pt", "eta", "phi", "e", "btag"],
    "ph1": ["pt", "eta", "phi", "e"],
    "ph2": ["pt", "eta", "phi", "e"],
    "met": ["pt", "eta", "phi", "e"],
}

_VEC_RESULT_VARS = ["mass", "pt", "eta", "phi", "e"]

_OBS_ROW_COUNT: dict[int, int] = {}  # nid -> current number of term rows

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
    """Delete a link when it is right-clicked; push undo entry first."""
    if not dpg.does_item_exist("node_editor_container"):
        return
    if not dpg.is_item_hovered("node_editor_container"):
        return
    for link_id in list(REGISTRY.links.keys()):
        if dpg.does_item_exist(link_id) and dpg.is_item_hovered(link_id):
            snap = _snapshot_link(link_id)
            if snap:
                _UNDO_HISTORY.append(snap)
            delink_callback(None, link_id)
            break


_PAN_SPEED = 20  # pixels per scroll notch

# Right-click drag-to-pan tracking (no boolean flag — check button state directly)
_PAN_LAST  = [0.0, 0.0]
_PAN_START = [0.0, 0.0]  # used to distinguish click vs drag for link deletion


def _pan_all_nodes(dx: float, dy: float):
    for nid in list(REGISTRY.nodes.keys()):
        node_tag = f"node_{nid}"
        if dpg.does_item_exist(node_tag):
            x, y = dpg.get_item_pos(node_tag)
            dpg.set_item_pos(node_tag, [x + dx, y + dy])


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
    _pan_all_nodes(dx, dy)


def _on_canvas_mouse_down(sender=None, app_data=None, user_data=None):
    """Record the right-button press position for pan delta and click detection."""
    if app_data != 1:  # right button only
        return
    pos = dpg.get_mouse_pos(local=False)
    _PAN_LAST[0], _PAN_LAST[1] = pos[0], pos[1]
    _PAN_START[0], _PAN_START[1] = pos[0], pos[1]


def _on_canvas_mouse_release(sender=None, app_data=None, user_data=None):
    """On right-button release: if mouse barely moved treat as click to delete link."""
    if app_data != 1:
        return
    pos = dpg.get_mouse_pos(local=False)
    dx = pos[0] - _PAN_START[0]
    dy = pos[1] - _PAN_START[1]
    if dx * dx + dy * dy < 25:  # < 5 px → right-click
        _on_right_click_link()


def _on_canvas_mouse_move(sender=None, app_data=None, user_data=None):
    """Translate all nodes while the right mouse button is held (drag-to-pan)."""
    if not dpg.is_mouse_button_down(dpg.mvMouseButton_Right):
        return
    pos = dpg.get_mouse_pos(local=False)
    dx = pos[0] - _PAN_LAST[0]
    dy = pos[1] - _PAN_LAST[1]
    _PAN_LAST[0], _PAN_LAST[1] = pos[0], pos[1]
    if abs(dx) < 100 and abs(dy) < 100:  # guard against cursor jump on first frame
        _pan_all_nodes(dx, dy)


# ---------------------------------------------------------------------------
# Undo history (max 10 entries)
# ---------------------------------------------------------------------------

_UNDO_HISTORY: deque = deque(maxlen=10)

_WIDGET_PREFIXES = (
    "cb_energy_", "cb_detector_",
    "cb_ltype_", "txt_leptons_", "txt_jets_", "txt_photons_",
    "txt_sel_", "txt_obs_", "obs_expr_",
    "cb_target_", "txt_bins_", "txt_range_min_", "txt_range_max_",
)

_INPUT_PREFIXES = (
    "txt_sel_", "txt_obs_", "txt_name_",
    "txt_bins_", "txt_range_min_", "txt_range_max_",
    "txt_leptons_", "txt_jets_", "txt_photons_",
)


def _any_input_active() -> bool:
    """Return True if any text/number input field is currently being edited."""
    for nid in REGISTRY.nodes:
        for prefix in _INPUT_PREFIXES:
            tag = f"{prefix}{nid}"
            if dpg.does_item_exist(tag) and dpg.is_item_active(tag):
                return True
    return False


def _snapshot_link(link_id: int) -> dict | None:
    if link_id not in REGISTRY.links:
        return None
    start_slot, end_slot = REGISTRY.links[link_id]
    src_nid = REGISTRY.slot_node.get(start_slot)
    dst_nid = REGISTRY.slot_node.get(end_slot)
    if src_nid is None or dst_nid is None:
        return None
    return {'type': 'link', 'src_nid': src_nid, 'dst_nid': dst_nid}


def _snapshot_node(nid: int) -> dict | None:
    node_type = REGISTRY.nodes.get(nid)
    if node_type is None:
        return None
    node_tag = f"node_{nid}"
    if not dpg.does_item_exist(node_tag):
        return None
    pos = dpg.get_item_pos(node_tag)
    name = REGISTRY.node_names.get(nid, "")
    values = {}
    for prefix in _WIDGET_PREFIXES:
        tag = f"{prefix}{nid}"
        if dpg.does_item_exist(tag):
            values[tag] = dpg.get_value(tag)
    # For typed Observable nodes, save per-row combo values
    n_rows = _OBS_ROW_COUNT.get(nid, 1)
    if node_type == "ObsGlobal":
        for i in range(n_rows):
            t = f"obs_g_var_{nid}_{i}"
            if dpg.does_item_exist(t):
                values[t] = dpg.get_value(t)
    elif node_type == "ObsObject":
        for i in range(n_rows):
            for pfx in (f"obs_o_obj_{nid}_{i}", f"obs_o_var_{nid}_{i}"):
                if dpg.does_item_exist(pfx):
                    values[pfx] = dpg.get_value(pfx)
    elif node_type == "ObsVectorSum":
        n_rows_v = _OBS_ROW_COUNT.get(nid, 2)
        t = f"obs_v_res_{nid}"
        if dpg.does_item_exist(t):
            values[t] = dpg.get_value(t)
        for i in range(n_rows_v):
            t = f"obs_v_obj_{nid}_{i}"
            if dpg.does_item_exist(t):
                values[t] = dpg.get_value(t)
    links = []
    for _, (start_slot, end_slot) in REGISTRY.links.items():
        s = REGISTRY.slot_node.get(start_slot)
        e = REGISTRY.slot_node.get(end_slot)
        if s == nid or e == nid:
            links.append({'src_nid': s, 'dst_nid': e})
    return {
        'type': 'node', 'nid': nid, 'node_type': node_type,
        'pos': list(pos), 'name': name, 'values': values, 'links': links,
    }


def _restore_link(snap: dict):
    src_nid, dst_nid = snap['src_nid'], snap['dst_nid']
    out_tag, in_tag = f"slot_out_{src_nid}", f"slot_in_{dst_nid}"
    if not (dpg.does_item_exist(out_tag) and dpg.does_item_exist(in_tag)):
        return
    try:
        start_slot = dpg.get_alias_id(out_tag)
        end_slot = dpg.get_alias_id(in_tag)
        # Check if this exact (start, end) pair already exists (supports fan-out)
        if any(s == start_slot and e == end_slot for s, e in REGISTRY.links.values()):
            return
        lid = dpg.add_node_link(start_slot, end_slot, parent="node_editor_container")
        REGISTRY.links[lid] = (start_slot, end_slot)
        REGISTRY.connections[start_slot] = end_slot
    except Exception:
        pass


def _restore_node(snap: dict):
    nid = snap['nid']
    if nid in REGISTRY.nodes:
        return  # nid collision — skip to avoid corruption
    old_next = REGISTRY.next_id
    REGISTRY.next_id = nid
    created = create_node(snap['node_type'], pos=snap['pos'],
                          name=snap['name'] if snap['name'] else None)
    REGISTRY.next_id = max(old_next, nid + 1)
    if created is None:
        return
    for tag, val in snap['values'].items():
        if dpg.does_item_exist(tag):
            try:
                dpg.set_value(tag, val)
            except Exception:
                pass
    # obs_expr_{nid} is already restored above; ensure variable combo is consistent
    ntype = snap['node_type']
    if ntype == "ObsObject":
        for i in range(_OBS_ROW_COUNT.get(nid, 1)):
            obj_tag = f"obs_o_obj_{nid}_{i}"
            var_tag = f"obs_o_var_{nid}_{i}"
            if dpg.does_item_exist(obj_tag) and dpg.does_item_exist(var_tag):
                obj = dpg.get_value(obj_tag)
                valid_vars = _OBJ_VARS.get(obj, ["pt", "eta", "phi", "e"])
                dpg.configure_item(var_tag, items=valid_vars)
    for link_snap in snap['links']:
        _restore_link(link_snap)


def undo_last():
    if not _UNDO_HISTORY:
        return
    entry = _UNDO_HISTORY.pop()
    if entry['type'] == 'node':
        _restore_node(entry)
    elif entry['type'] == 'link':
        _restore_link(entry)
    elif entry['type'] == 'batch':
        for item in reversed(entry['items']):
            if item['type'] == 'node':
                _restore_node(item)
            elif item['type'] == 'link':
                _restore_link(item)


def _on_key_undo(sender=None, app_data=None, user_data=None):
    if not (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
        return
    if _any_input_active():
        return
    undo_last()


def _on_key_delete(sender=None, app_data=None, user_data=None):
    """Delete selected nodes and links; block when an input field is active."""
    if _any_input_active():
        return
    if not dpg.does_item_exist("node_editor_container"):
        return
    batch = []
    for dpg_id in list(dpg.get_selected_nodes("node_editor_container")):
        for nid in list(REGISTRY.nodes.keys()):
            try:
                if dpg.does_item_exist(f"node_{nid}") and dpg.get_alias_id(f"node_{nid}") == dpg_id:
                    snap = _snapshot_node(nid)
                    if snap:
                        batch.append(snap)
                    delete_node(nid, _push_undo=False)
                    break
            except Exception:
                pass
    for link_id in list(dpg.get_selected_links("node_editor_container")):
        snap = _snapshot_link(link_id)
        if snap:
            batch.append(snap)
        delink_callback(None, link_id)
    if batch:
        _UNDO_HISTORY.append(batch[0] if len(batch) == 1 else {'type': 'batch', 'items': batch})


def setup_link_handlers():
    with dpg.handler_registry():
        dpg.add_mouse_wheel_handler(callback=_on_wheel_pan)
        dpg.add_mouse_down_handler(
            button=dpg.mvMouseButton_Right,
            callback=_on_canvas_mouse_down,
        )
        dpg.add_mouse_release_handler(
            button=dpg.mvMouseButton_Right,
            callback=_on_canvas_mouse_release,
        )
        dpg.add_mouse_move_handler(callback=_on_canvas_mouse_move)
        dpg.add_key_press_handler(key=dpg.mvKey_Back,   callback=_on_key_delete)
        dpg.add_key_press_handler(key=dpg.mvKey_Delete, callback=_on_key_delete)
        dpg.add_key_press_handler(key=dpg.mvKey_Z,      callback=_on_key_undo)


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


# ---------------------------------------------------------------------------
# Runtime node state highlighting (active / done) — applied from main thread
# ---------------------------------------------------------------------------

_NODE_RUNTIME_STATES: dict[int, str] = {}     # nid -> "active" | "done"
_NODE_RUNTIME_THEME_IDS: dict[int, int] = {}  # nid -> DPG theme item id (integer UUID)


def _delete_runtime_theme(nid: int):
    """Delete the DPG theme item previously created for nid's runtime highlight."""
    old_id = _NODE_RUNTIME_THEME_IDS.pop(nid, None)
    if old_id is not None and dpg.does_item_exist(old_id):
        dpg.delete_item(old_id)


def _set_node_active(nid: int):
    """Apply an orange/amber theme to indicate the node is currently processing."""
    node_tag = f"node_{nid}"
    if not dpg.does_item_exist(node_tag):
        return
    _delete_runtime_theme(nid)
    theme_id = dpg.generate_uuid()
    with dpg.theme(tag=theme_id):
        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackground,
                                (75, 50, 8), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundHovered,
                                (95, 65, 12), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected,
                                (95, 65, 12), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeOutline,
                                (215, 145, 25), category=dpg.mvThemeCat_Nodes)
    dpg.bind_item_theme(node_tag, theme_id)
    _NODE_RUNTIME_THEME_IDS[nid] = theme_id
    _NODE_RUNTIME_STATES[nid] = "active"


def _set_node_aborted(nid: int):
    """Apply a red theme to indicate the node was processing when the run was stopped."""
    node_tag = f"node_{nid}"
    if not dpg.does_item_exist(node_tag):
        return
    _delete_runtime_theme(nid)
    theme_id = dpg.generate_uuid()
    with dpg.theme(tag=theme_id):
        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackground,
                                (75, 15, 15), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundHovered,
                                (95, 20, 20), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected,
                                (95, 20, 20), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeOutline,
                                (210, 50, 50), category=dpg.mvThemeCat_Nodes)
    dpg.bind_item_theme(node_tag, theme_id)
    _NODE_RUNTIME_THEME_IDS[nid] = theme_id
    _NODE_RUNTIME_STATES[nid] = "aborted"


def _set_node_done(nid: int):
    """Apply a green theme to indicate the node completed successfully."""
    node_tag = f"node_{nid}"
    if not dpg.does_item_exist(node_tag):
        return
    _delete_runtime_theme(nid)
    theme_id = dpg.generate_uuid()
    with dpg.theme(tag=theme_id):
        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackground,
                                (15, 58, 22), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundHovered,
                                (20, 74, 30), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected,
                                (20, 74, 30), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeOutline,
                                (48, 195, 70), category=dpg.mvThemeCat_Nodes)
    dpg.bind_item_theme(node_tag, theme_id)
    _NODE_RUNTIME_THEME_IDS[nid] = theme_id
    _NODE_RUNTIME_STATES[nid] = "done"


def _clear_node_runtime_theme(nid: int):
    """Remove the runtime theme from a node and restore its default appearance."""
    node_tag = f"node_{nid}"
    _delete_runtime_theme(nid)
    if dpg.does_item_exist(node_tag):
        dpg.bind_item_theme(node_tag, 0)
    _NODE_RUNTIME_STATES.pop(nid, None)


def clear_all_node_runtime_states():
    """Reset every node to its default appearance (no runtime highlight)."""
    for nid in list(REGISTRY.nodes.keys()):
        _clear_node_runtime_theme(nid)
    _NODE_RUNTIME_STATES.clear()
    _NODE_RUNTIME_THEME_IDS.clear()


def apply_node_runtime_states(active_nodes: set, completed_nodes: set,
                              stopped: bool = False):
    """Called from the main-thread poll loop to sync node visuals with run state.

    When stopped=True, nodes still in active_nodes (not yet finished) are
    coloured red to signal they were interrupted rather than left orange.
    """
    for nid in list(REGISTRY.nodes.keys()):
        current = _NODE_RUNTIME_STATES.get(nid)
        if nid in active_nodes:
            if stopped:
                if current != "aborted":
                    _set_node_aborted(nid)
            else:
                if current != "active":
                    _set_node_active(nid)
        elif nid in completed_nodes:
            if current != "done":
                _set_node_done(nid)
        else:
            if current is not None:
                _clear_node_runtime_theme(nid)


def validate_node_expressions() -> list[tuple[int, str]]:
    """Check syntax of all expression fields. Returns list of (nid, error_msg)."""
    errors = []
    for nid, ntype in REGISTRY.nodes.items():
        if ntype == "Selection":
            field = f"txt_sel_{nid}"
        elif ntype in ("Observable", "ObsCustom"):
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
    connected_starts = {s for s, _ in REGISTRY.links.values()}
    connected_ends   = {e for _, e in REGISTRY.links.values()}
    for nid in all_nids:
        if nid in error_nids:
            ntype = REGISTRY.nodes.get(nid, "")
            parts = []
            if ntype == "DataSource":
                sid = _slot_id(f"slot_out_{nid}")
                if sid is None or sid not in connected_starts:
                    parts.append("output not connected")
            elif ntype == "Multiplicity":
                sid = _slot_id(f"slot_out_{nid}")
                if sid is None or sid not in connected_starts:
                    parts.append("output not connected")
                sid = _slot_id(f"slot_in_{nid}")
                if sid is None or sid not in connected_ends:
                    parts.append("input not connected")
            elif ntype == "Selection":
                sid = _slot_id(f"slot_in_{nid}")
                if sid is None or sid not in connected_ends:
                    parts.append("input not connected")
            elif _is_obs(ntype):
                sid = _slot_id(f"slot_out_{nid}")
                if sid is None or sid not in connected_starts:
                    parts.append("output not connected (connect to a Histogram)")
                sid = _slot_id(f"slot_in_{nid}")
                if sid is None or sid not in connected_ends:
                    parts.append("input not connected (connect from a Selection)")
            elif ntype == "Histogram":
                sid = _slot_id(f"slot_in_{nid}")
                if sid is None or sid not in connected_ends:
                    parts.append("input not connected (connect from an Observable)")
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


# ---------------------------------------------------------------------------
# Typed Observable node helpers
# ---------------------------------------------------------------------------

def _build_obs_expr(nid: int, subtype: str):
    """Rebuild the observable expression from combo widgets and store it."""
    expr_tag = f"obs_expr_{nid}"
    if not dpg.does_item_exist(expr_tag):
        return
    n = _OBS_ROW_COUNT.get(nid, 1)

    if subtype == "ObsGlobal":
        terms = []
        for i in range(n):
            t = f"obs_g_var_{nid}_{i}"
            if dpg.does_item_exist(t):
                v = dpg.get_value(t).strip()
                if v:
                    terms.append(v)
        expr = " + ".join(terms) if terms else _GLOBAL_VARS[0]

    elif subtype == "ObsObject":
        terms = []
        for i in range(n):
            ot = f"obs_o_obj_{nid}_{i}"
            vt = f"obs_o_var_{nid}_{i}"
            if dpg.does_item_exist(ot) and dpg.does_item_exist(vt):
                obj = dpg.get_value(ot)
                var = dpg.get_value(vt)
                if obj and var:
                    terms.append(f"{obj}.{var}")
        expr = " + ".join(terms) if terms else "met.pt"

    elif subtype == "ObsVectorSum":
        n_v = _OBS_ROW_COUNT.get(nid, 2)
        res_t = f"obs_v_res_{nid}"
        result_var = dpg.get_value(res_t) if dpg.does_item_exist(res_t) else "mass"
        objs = []
        for i in range(n_v):
            ot = f"obs_v_obj_{nid}_{i}"
            if dpg.does_item_exist(ot):
                o = dpg.get_value(ot)
                if o:
                    objs.append(o)
        if len(objs) >= 2:
            p4_sum = " + ".join(f"{o}.p4" for o in objs)
            expr = f"({p4_sum}).{result_var}"
        elif len(objs) == 1:
            expr = f"{objs[0]}.{result_var}"
        else:
            expr = f"(l1.p4 + l2.p4).{result_var}"

    else:
        return

    dpg.set_value(expr_tag, expr)


def _obs_obj_change(nid: int, row_idx: int, obj_val: str):
    """Update variable combo when object selection changes."""
    var_tag = f"obs_o_var_{nid}_{row_idx}"
    if not dpg.does_item_exist(var_tag):
        return
    valid_vars = _OBJ_VARS.get(obj_val, ["pt", "eta", "phi", "e"])
    current = dpg.get_value(var_tag)
    dpg.configure_item(var_tag, items=valid_vars)
    if current not in valid_vars:
        dpg.set_value(var_tag, valid_vars[0])
    _build_obs_expr(nid, "ObsObject")


def _obs_add_global_row(nid: int):
    n = _OBS_ROW_COUNT.get(nid, 1)
    _OBS_ROW_COUNT[nid] = n + 1
    rows_grp = f"obs_g_rows_grp_{nid}"
    if not dpg.does_item_exist(rows_grp):
        return
    prev_row = f"obs_g_row_{nid}_{n - 1}"
    if dpg.does_item_exist(prev_row):
        dpg.add_text("+", parent=prev_row)
    new_row = dpg.add_group(horizontal=True, tag=f"obs_g_row_{nid}_{n}", parent=rows_grp)
    dpg.add_combo(
        _GLOBAL_VARS, default_value=_GLOBAL_VARS[0],
        tag=f"obs_g_var_{nid}_{n}", width=100,
        callback=lambda s, a, u: _build_obs_expr(u, "ObsGlobal"),
        user_data=nid, parent=new_row,
    )
    _build_obs_expr(nid, "ObsGlobal")


def _obs_add_object_row(nid: int):
    n = _OBS_ROW_COUNT.get(nid, 1)
    _OBS_ROW_COUNT[nid] = n + 1
    rows_grp = f"obs_o_rows_grp_{nid}"
    if not dpg.does_item_exist(rows_grp):
        return
    prev_row = f"obs_o_row_{nid}_{n - 1}"
    if dpg.does_item_exist(prev_row):
        dpg.add_text("+", parent=prev_row)
    new_row = dpg.add_group(horizontal=True, tag=f"obs_o_row_{nid}_{n}", parent=rows_grp)
    dpg.add_combo(
        list(_OBJ_VARS.keys()), default_value="l1",
        tag=f"obs_o_obj_{nid}_{n}", width=55,
        callback=lambda s, a, u: _obs_obj_change(u[0], u[1], a),
        user_data=(nid, n), parent=new_row,
    )
    dpg.add_combo(
        _OBJ_VARS["l1"], default_value="pt",
        tag=f"obs_o_var_{nid}_{n}", width=60,
        callback=lambda s, a, u: _build_obs_expr(u, "ObsObject"),
        user_data=nid, parent=new_row,
    )
    _build_obs_expr(nid, "ObsObject")


def _obs_add_vecsum_row(nid: int):
    n = _OBS_ROW_COUNT.get(nid, 2)
    _OBS_ROW_COUNT[nid] = n + 1
    rows_grp = f"obs_v_rows_grp_{nid}"
    if not dpg.does_item_exist(rows_grp):
        return
    prev_row = f"obs_v_row_{nid}_{n - 1}"
    if dpg.does_item_exist(prev_row):
        dpg.add_text("+", parent=prev_row)
    new_row = dpg.add_group(horizontal=True, tag=f"obs_v_row_{nid}_{n}", parent=rows_grp)
    dpg.add_combo(
        list(_OBJ_VARS.keys()), default_value="l1",
        tag=f"obs_v_obj_{nid}_{n}", width=55,
        callback=lambda s, a, u: _build_obs_expr(u, "ObsVectorSum"),
        user_data=nid, parent=new_row,
    )
    _build_obs_expr(nid, "ObsVectorSum")


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

def delete_node(nid: int, _push_undo: bool = True):
    if _push_undo:
        snap = _snapshot_node(nid)
        if snap:
            _UNDO_HISTORY.append(snap)

    node_slots = {f"slot_in_{nid}", f"slot_out_{nid}"}
    for k, v in list(REGISTRY.slot_node.items()):
        if v == nid:
            node_slots.add(k)

    dead = [lid for lid, (s, e) in REGISTRY.links.items()
            if s in node_slots or e in node_slots]
    for lid in dead:
        start, _ = REGISTRY.links.pop(lid)
        REGISTRY.connections.pop(start, None)
        # DPG does not auto-remove link items when a node is deleted; orphaned
        # link items referencing the deleted attributes cause a DPG crash.
        if dpg.does_item_exist(lid):
            dpg.delete_item(lid)

    for k in list(REGISTRY.slot_node):
        if REGISTRY.slot_node[k] == nid:
            del REGISTRY.slot_node[k]

    REGISTRY.nodes.pop(nid, None)
    REGISTRY.node_names.pop(nid, None)

    handler_tag = f"name_handler_{nid}"
    if dpg.does_item_exist(handler_tag):
        dpg.delete_item(handler_tag)

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

    elif node_type in ("Observable", "ObsCustom"):
        _make_expr_widgets(
            tag=f"txt_obs_{nid}",
            default="met.pt",
            hint="e.g.  (l1.p4+l2.p4).mass",
            nid=nid, parent_tag=parent_tag, width=200,
        )

    elif node_type == "ObsGlobal":
        _OBS_ROW_COUNT[nid] = 1
        rows_grp = f"obs_g_rows_grp_{nid}"
        dpg.add_group(tag=rows_grp, parent=parent_tag)
        new_row = dpg.add_group(horizontal=True, tag=f"obs_g_row_{nid}_0", parent=rows_grp)
        dpg.add_combo(
            _GLOBAL_VARS, default_value=_GLOBAL_VARS[0],
            tag=f"obs_g_var_{nid}_0", width=100,
            callback=lambda s, a, u: _build_obs_expr(u, "ObsGlobal"),
            user_data=nid, parent=new_row,
        )
        dpg.add_button(
            label="+", small=True, parent=parent_tag,
            callback=lambda s, a, u: _obs_add_global_row(u),
            user_data=nid,
        )
        dpg.add_input_text(
            tag=f"obs_expr_{nid}", default_value=_GLOBAL_VARS[0],
            show=False, parent=parent_tag,
        )
        _build_obs_expr(nid, "ObsGlobal")

    elif node_type == "ObsObject":
        _OBS_ROW_COUNT[nid] = 1
        default_obj, default_var = "met", "pt"
        rows_grp = f"obs_o_rows_grp_{nid}"
        dpg.add_group(tag=rows_grp, parent=parent_tag)
        new_row = dpg.add_group(horizontal=True, tag=f"obs_o_row_{nid}_0", parent=rows_grp)
        dpg.add_combo(
            list(_OBJ_VARS.keys()), default_value=default_obj,
            tag=f"obs_o_obj_{nid}_0", width=55,
            callback=lambda s, a, u: _obs_obj_change(u[0], u[1], a),
            user_data=(nid, 0), parent=new_row,
        )
        dpg.add_combo(
            _OBJ_VARS[default_obj], default_value=default_var,
            tag=f"obs_o_var_{nid}_0", width=60,
            callback=lambda s, a, u: _build_obs_expr(u, "ObsObject"),
            user_data=nid, parent=new_row,
        )
        dpg.add_button(
            label="+", small=True, parent=parent_tag,
            callback=lambda s, a, u: _obs_add_object_row(u),
            user_data=nid,
        )
        dpg.add_input_text(
            tag=f"obs_expr_{nid}", default_value=f"{default_obj}.{default_var}",
            show=False, parent=parent_tag,
        )
        _build_obs_expr(nid, "ObsObject")

    elif node_type == "ObsVectorSum":
        _OBS_ROW_COUNT[nid] = 2
        default_objs = ["l1", "l2"]
        # Result property at top
        dpg.add_combo(
            _VEC_RESULT_VARS, default_value="mass",
            tag=f"obs_v_res_{nid}", width=80,
            callback=lambda s, a, u: _build_obs_expr(u, "ObsVectorSum"),
            user_data=nid, parent=parent_tag,
        )
        # Object rows
        rows_grp = f"obs_v_rows_grp_{nid}"
        dpg.add_group(tag=rows_grp, parent=parent_tag)
        for row_idx in range(2):
            new_row = dpg.add_group(horizontal=True, tag=f"obs_v_row_{nid}_{row_idx}", parent=rows_grp)
            dpg.add_combo(
                list(_OBJ_VARS.keys()), default_value=default_objs[row_idx],
                tag=f"obs_v_obj_{nid}_{row_idx}", width=55,
                callback=lambda s, a, u: _build_obs_expr(u, "ObsVectorSum"),
                user_data=nid, parent=new_row,
            )
            if row_idx == 0:
                dpg.add_text("+", parent=new_row)
        dpg.add_button(
            label="+", small=True, parent=parent_tag,
            callback=lambda s, a, u: _obs_add_vecsum_row(u),
            user_data=nid,
        )
        dpg.add_input_text(
            tag=f"obs_expr_{nid}", default_value="(l1.p4 + l2.p4).mass",
            show=False, parent=parent_tag,
        )
        _build_obs_expr(nid, "ObsVectorSum")

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
# Node name editing
# ---------------------------------------------------------------------------

_ICON_EDIT = "✏"  # ✏ PENCIL       -- shown when node is in view mode
_ICON_SAVE = "✔"  # ✔ HEAVY CHECK  -- shown when node is in edit mode


def _save_node_name(nid: int):
    """Persist the current input value and return the node to view mode.

    Safe to call even if the edit field is already hidden (no-op in that case).
    """
    edit_tag  = f"txt_name_{nid}"
    label_tag = f"lbl_name_{nid}"
    btn_tag   = f"btn_name_{nid}"
    if not dpg.does_item_exist(edit_tag) or not dpg.is_item_visible(edit_tag):
        return  # already in view mode — nothing to do
    new_name = dpg.get_value(edit_tag).strip()
    REGISTRY.node_names[nid] = new_name
    if dpg.does_item_exist(label_tag):
        display = new_name if new_name else NODE_LABELS.get(REGISTRY.nodes.get(nid, ""), "")
        dpg.set_value(label_tag, display)
        dpg.configure_item(label_tag, show=True)
    dpg.configure_item(edit_tag, show=False)
    if dpg.does_item_exist(btn_tag):
        dpg.configure_item(btn_tag, label=_ICON_EDIT)


def _on_name_edit_click(sender, app_data, user_data):
    """Toggle between view mode (writing-hand) and edit mode (floppy-disk)."""
    nid = user_data
    edit_tag  = f"txt_name_{nid}"
    label_tag = f"lbl_name_{nid}"
    btn_tag   = f"btn_name_{nid}"
    if not dpg.does_item_exist(edit_tag):
        return
    if dpg.is_item_visible(edit_tag):
        # Floppy disk clicked — save and exit edit mode
        _save_node_name(nid)
    else:
        # Writing-hand clicked — enter edit mode
        dpg.set_value(edit_tag, REGISTRY.node_names.get(nid, ""))
        dpg.configure_item(label_tag, show=False)
        dpg.configure_item(edit_tag,  show=True)
        if dpg.does_item_exist(btn_tag):
            dpg.configure_item(btn_tag, label=_ICON_SAVE)
        dpg.focus_item(edit_tag)


def _on_name_changed(sender, app_data, user_data):
    """Called when Enter is pressed — delegates to _save_node_name."""
    _save_node_name(user_data)


def _on_name_deactivated(sender, app_data, user_data):
    """Called when the input field loses focus — saves name (click-away)."""
    _save_node_name(user_data)


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


_HAS_HELP = {"Selection", "Observable", "ObsCustom"}

# Estimated pixel width of the widest content widget per node type.
# Used to right-align the × / ? buttons in the name row.
_NODE_CONTENT_PX = {
    "DataSource":   195,
    "Multiplicity": 200,
    "Selection":    250,
    "Observable":   228,
    "ObsGlobal":    155,
    "ObsObject":    160,
    "ObsVectorSum": 170,
    "ObsCustom":    228,
    "Histogram":    208,
}
_CHAR_PX   = 7   # approximate pixels per character (default DPG font ~13px)
_EDIT_BTN  = 20  # ✏ / ✔ small button width
_CLOSE_BTN = 18  # small button width (×, ?, !)


def _name_btn_spacer(node_type: str, display_name: str) -> int:
    content_w = _NODE_CONTENT_PX.get(node_type, 200)
    name_w    = len(display_name) * _CHAR_PX
    btn_w     = (_CLOSE_BTN * 2) if node_type in _HAS_HELP else _CLOSE_BTN  # ? + × (! hidden)
    spacer    = content_w - name_w - _EDIT_BTN - btn_w
    return max(5, spacer)


def create_node(node_type: str, pos: list | None = None, name: str | None = None):
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

    # ── Name row — editable label + close/help buttons at the right ──────
    display_name = name if name else NODE_LABELS.get(node_type, node_type)
    if name:
        REGISTRY.node_names[nid] = name
    name_attr = f"slot_name_{nid}"
    dpg.add_node_attribute(
        attribute_type=dpg.mvNode_Attr_Static,
        tag=name_attr, parent=node_tag,
    )
    name_grp = f"grp_name_{nid}"
    dpg.add_group(horizontal=True, tag=name_grp, parent=name_attr)
    dpg.add_text(display_name, tag=f"lbl_name_{nid}", parent=name_grp)
    dpg.add_button(
        label=_ICON_EDIT, tag=f"btn_name_{nid}", small=True,
        callback=_on_name_edit_click,
        user_data=nid, parent=name_grp,
    )
    if _state.EXTENDED_FONT is not None:
        dpg.bind_item_font(f"btn_name_{nid}", _state.EXTENDED_FONT)
    dpg.add_input_text(
        tag=f"txt_name_{nid}", width=110, show=False,
        hint="custom name", on_enter=True,
        callback=_on_name_changed,
        user_data=nid, parent=name_grp,
    )
    # Bind deactivated handler so clicking away also saves the name
    with dpg.item_handler_registry(tag=f"name_handler_{nid}"):
        dpg.add_item_deactivated_handler(
            callback=_on_name_deactivated,
            user_data=nid,
        )
    dpg.bind_item_handler_registry(f"txt_name_{nid}", f"name_handler_{nid}")

    # Spacer pushes ×/? buttons toward the right edge of the node
    dpg.add_spacer(width=_name_btn_spacer(node_type, display_name), parent=name_grp)

    if node_type in _HAS_HELP:
        dpg.add_button(
            label="!", tag=f"btn_bang_{nid}", small=True, show=False,
            callback=_show_node_error,
            user_data=nid, parent=name_grp,
        )
        dpg.add_button(
            label="?", tag=f"btn_help_{nid}", small=True,
            callback=_show_help_window,
            parent=name_grp,
        )

    dpg.add_button(
        label="×", tag=f"btn_close_{nid}", small=True,
        callback=lambda s, a, u: delete_node(u),
        user_data=nid, parent=name_grp,
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
# Node palette drag-and-drop drop handler
# ---------------------------------------------------------------------------

def on_node_editor_drop(sender, app_data, user_data):
    """Create a node at the drop position using screen-to-pane coordinate mapping.

    dpg.get_item_pos reads state['pos'] which for child_window items returns
    the screen-space position (unlike rect_min, which is only populated for
    non-container ImGui items).
    """
    node_type = app_data
    if not node_type or not isinstance(node_type, str):
        return
    screen = dpg.get_mouse_pos(local=False)
    origin = dpg.get_item_pos("node_editor_pane")
    x = max(10, int(screen[0] - origin[0]))
    y = max(10, int(screen[1] - origin[1]))
    create_node(node_type, pos=[x, y])


# ---------------------------------------------------------------------------
# Graph topology compiler
# ---------------------------------------------------------------------------

def compile_graph_topology() -> dict:
    import hashlib

    nodes = REGISTRY.nodes

    ds_nids  = [n for n, t in nodes.items() if t == "DataSource"]
    mul_nids = [n for n, t in nodes.items() if t == "Multiplicity"]
    sel_nids = [n for n, t in nodes.items() if t == "Selection"]
    obs_nids = [n for n, t in nodes.items() if _is_obs(t)]

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

    # Build per-node successor/predecessor maps from all links
    node_successors   = {}  # nid -> [nid, ...]
    node_predecessors = {}  # nid -> [nid, ...]
    for _, (start_slot, end_slot) in REGISTRY.links.items():
        s_nid = REGISTRY.slot_node.get(start_slot)
        e_nid = REGISTRY.slot_node.get(end_slot)
        if s_nid is not None and e_nid is not None:
            node_successors.setdefault(s_nid, []).append(e_nid)
            node_predecessors.setdefault(e_nid, []).append(s_nid)

    # Selection branch roots: Selection nodes whose parent is NOT another Selection
    sel_branch_roots = [
        nid for nid in sel_nids
        if not any(nodes.get(p) == "Selection" for p in node_predecessors.get(nid, []))
    ]
    if not sel_branch_roots and sel_nids:
        sel_branch_roots = [sel_nids[0]]

    # For each branch root BFS-collect the full Selection chain (handles AND-chaining)
    def _collect_chain(root):
        chain, visited, queue = [], set(), [root]
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            if nodes.get(nid) == "Selection":
                chain.append(nid)
                for succ in node_successors.get(nid, []):
                    if nodes.get(succ) == "Selection" and succ not in visited:
                        queue.append(succ)
        return chain

    branch_chains = {root: _collect_chain(root) for root in sel_branch_roots}

    # Map each Selection nid back to its branch root
    sel_to_branch = {
        nid: root
        for root, chain in branch_chains.items()
        for nid in chain
    }

    # Find which Selection each Observable is directly connected to
    obs_to_sel = {}  # obs_nid -> sel_nid
    for _, (start_slot, end_slot) in REGISTRY.links.items():
        s_nid = REGISTRY.slot_node.get(start_slot)
        e_nid = REGISTRY.slot_node.get(end_slot)
        if (s_nid is not None and e_nid is not None
                and nodes.get(s_nid) == "Selection"
                and _is_obs(nodes.get(e_nid))):
            obs_to_sel[e_nid] = s_nid

    # Find Observable -> Histogram pairs from graph links
    obs_hist_pairs = []
    for _, (start_slot, end_slot) in REGISTRY.links.items():
        s_nid = REGISTRY.slot_node.get(start_slot)
        e_nid = REGISTRY.slot_node.get(end_slot)
        if (s_nid is not None and e_nid is not None
                and _is_obs(nodes.get(s_nid))
                and nodes.get(e_nid) == "Histogram"):
            obs_hist_pairs.append((s_nid, e_nid))

    # Fallback when no links exist (e.g., only one of each node type, unlinked)
    if not obs_hist_pairs:
        obs_nid  = obs_nids[0] if obs_nids else None
        hist_nid = next((n for n, t in nodes.items() if t == "Histogram"), None)
        if obs_nid is not None and hist_nid is not None:
            obs_hist_pairs = [(obs_nid, hist_nid)]

    # Compute the prefix chain from branch root up to (and including) a given sel_nid.
    # For an Observable directly connected to Selection_A in a chain [A, B], this
    # returns [A] so that only expr_A is applied — not the full [A, B] chain.
    def _prefix_chain(sel_nid):
        root = sel_to_branch.get(sel_nid, sel_nid)
        full_chain = branch_chains.get(root, [sel_nid])
        try:
            pos = full_chain.index(sel_nid)
            return full_chain[:pos + 1]
        except ValueError:
            return [sel_nid]

    # Group histograms by their Observable's direct parent Selection.
    # This ensures Histogram_A (under Selection_A) uses only Selection_A's
    # expressions even when Selection_B is AND-chained from Selection_A.
    parent_sel_order = []   # ordered unique parent sel_nids (insertion order)
    parent_sel_hists = {}   # sel_nid -> [raw hcfg params dict, ...]
    unassigned = []

    for obs_nid, hist_nid in obs_hist_pairs:
        sel_nid = obs_to_sel.get(obs_nid)

        obs_ntype = nodes.get(obs_nid)
        if obs_ntype in ("Observable", "ObsCustom"):
            observable = (dpg.get_value(f"txt_obs_{obs_nid}").strip()
                          if dpg.does_item_exist(f"txt_obs_{obs_nid}") else "met.pt")
        else:
            observable = (dpg.get_value(f"obs_expr_{obs_nid}")
                          if dpg.does_item_exist(f"obs_expr_{obs_nid}") else "met.pt")
        target  = (dpg.get_value(f"cb_target_{hist_nid}")
                   if dpg.does_item_exist(f"cb_target_{hist_nid}") else "None")
        bins    = (str(dpg.get_value(f"txt_bins_{hist_nid}"))
                   if dpg.does_item_exist(f"txt_bins_{hist_nid}") else "40")
        rng_min = (str(dpg.get_value(f"txt_range_min_{hist_nid}"))
                   if dpg.does_item_exist(f"txt_range_min_{hist_nid}") else "0.0")
        rng_max = (str(dpg.get_value(f"txt_range_max_{hist_nid}"))
                   if dpg.does_item_exist(f"txt_range_max_{hist_nid}") else "150.0")
        node_name = REGISTRY.node_names.get(hist_nid, "")
        hcfg_raw = {
            "observable": observable, "bins": bins, "min": rng_min, "max": rng_max,
            "target": target, "node_name": node_name,
            "obs_nid": obs_nid, "hist_nid": hist_nid,
        }

        if sel_nid is not None:
            if sel_nid not in parent_sel_hists:
                parent_sel_order.append(sel_nid)
                parent_sel_hists[sel_nid] = []
            parent_sel_hists[sel_nid].append(hcfg_raw)
        else:
            unassigned.append(hcfg_raw)

    # Unassigned histograms (no connected Selection) fall back to the first branch root
    if unassigned and sel_branch_roots:
        first_root = sel_branch_roots[0]
        if first_root not in parent_sel_hists:
            parent_sel_order.append(first_root)
            parent_sel_hists[first_root] = []
        parent_sel_hists[first_root].extend(unassigned)

    # Sort parent_sel_order by (branch_root_nid, position_in_chain)
    def _sel_sort_key(sid):
        root = sel_to_branch.get(sid, sid)
        chain = branch_chains.get(root, [sid])
        try:
            pos = chain.index(sid)
        except ValueError:
            pos = 0
        return (root, pos)

    parent_sel_order.sort(key=_sel_sort_key)

    # Build selections list; each entry uses only the prefix chain for its parent sel_nid
    mult_h5_base = energy + detector + str(mult_cuts)
    plot_idx = 0
    selections = []

    for sel_nid in parent_sel_order:
        prefix = _prefix_chain(sel_nid)
        sel_exprs = [
            dpg.get_value(f"txt_sel_{n}").strip()
            for n in prefix
            if dpg.does_item_exist(f"txt_sel_{n}") and dpg.get_value(f"txt_sel_{n}").strip()
        ]
        h5_sel = hashlib.md5((mult_h5_base + str(sel_exprs)).encode()).hexdigest()

        histograms = []
        for hcfg_raw in parent_sel_hists.get(sel_nid, []):
            h5_full = hashlib.md5(
                (h5_sel + hcfg_raw["observable"] + hcfg_raw["bins"]
                 + hcfg_raw["min"] + hcfg_raw["max"] + hcfg_raw["target"]).encode()
            ).hexdigest()
            entry = dict(hcfg_raw)
            entry["h5"] = h5_full
            entry["plot_idx"] = plot_idx
            histograms.append(entry)
            plot_idx += 1

        sel_name = REGISTRY.node_names.get(sel_nid, "").strip()
        selections.append({
            "nid": sel_nid,
            "node_name": sel_name if sel_name else f"Selection {len(selections) + 1}",
            "sel_exprs": sel_exprs,
            "h5_sel": h5_sel,
            "histograms": histograms,
        })

    # Flatten for backward-compat fields (first selection, first histogram)
    first_sel  = selections[0] if selections else {
        "sel_exprs": [], "h5_sel": hashlib.md5(mult_h5_base.encode()).hexdigest(),
        "histograms": [],
    }
    first_hist = first_sel["histograms"][0] if first_sel["histograms"] else {
        "observable": "met.pt", "bins": "40", "min": "0.0", "max": "150.0",
        "target": "None", "h5": hashlib.md5(first_sel["h5_sel"].encode()).hexdigest(),
        "plot_idx": 0,
    }
    all_histograms = [h for sel in selections for h in sel["histograms"]]

    return {
        "energy": energy, "detector": detector,
        "observable": first_hist["observable"],
        "bins": first_hist["bins"], "min": first_hist["min"], "max": first_hist["max"],
        "target": first_hist["target"], "h5": first_hist["h5"],
        "h5_sel": first_sel["h5_sel"],
        "mult_cuts": mult_cuts, "sel_exprs": first_sel["sel_exprs"],
        "histograms": all_histograms,
        "selections": selections,
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
    """Return node ids that are not properly connected in the pipeline.

    Rules:
    - DataSource: output must be connected (starts the main chain).
    - Multiplicity/Selection: both input and output must be connected
      (form the DS->Mul->Sel cut chain). Selection output is OPTIONAL
      because Observables receive their data from the global selection,
      not from an explicit graph link.
    - Observable: output must be connected (to a Histogram). Input from
      Selection is optional and decorative — Observables apply the global
      selection automatically.
    - Histogram: input must be connected (from an Observable).
    """
    connected_starts = {s for s, _ in REGISTRY.links.values()}
    connected_ends   = {e for _, e in REGISTRY.links.values()}
    error_nids = []

    for nid, ntype in REGISTRY.nodes.items():
        if ntype == "DataSource":
            sid = _slot_id(f"slot_out_{nid}")
            if sid is None or sid not in connected_starts:
                error_nids.append(nid)

        elif ntype == "Multiplicity":
            for slot_tag in (f"slot_out_{nid}", f"slot_in_{nid}"):
                sid = _slot_id(slot_tag)
                pool = connected_starts if "out" in slot_tag else connected_ends
                if sid is None or sid not in pool:
                    error_nids.append(nid)

        elif ntype == "Selection":
            # Must have input (part of chain); output to Observable is optional
            sid = _slot_id(f"slot_in_{nid}")
            if sid is None or sid not in connected_ends:
                error_nids.append(nid)

        elif _is_obs(ntype):
            # Must have output to a Histogram and input from a Selection
            has_err = False
            sid = _slot_id(f"slot_out_{nid}")
            if sid is None or sid not in connected_starts:
                error_nids.append(nid)
                has_err = True
            sid = _slot_id(f"slot_in_{nid}")
            if sid is None or sid not in connected_ends:
                if not has_err:
                    error_nids.append(nid)

        elif ntype == "Histogram":
            sid = _slot_id(f"slot_in_{nid}")
            if sid is None or sid not in connected_ends:
                error_nids.append(nid)

    return list(set(error_nids))
