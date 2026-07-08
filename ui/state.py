import queue
import threading

STATE_LOCK = threading.RLock()

RUN_STATE = {
    "progress":        0.0,
    "running":         False,
    "stop":            False,
    "status_msg":      "Initialized.",
    "fit_mu":          None,
    "fit_sig":         None,
    "active_nodes":    set(),   # nids currently being processed (thread workers signal)
    "completed_nodes": set(),   # nids whose processing is finished (green)
    "current_phase":   "",      # short human-readable phase string for status label
    "run_start_time":  0.0,     # time.time() when current run started
}

NODE_HIERARCHY = {
    "DataSource":   0,
    "Multiplicity": 1,
    "Selection":    2,
    "Observable":   3,
    "Histogram":    4,
}

NODE_LABELS = {
    "DataSource":   "Data",
    "Multiplicity": "Multiplicity",
    "Selection":    "Selection",
    "Observable":   "Observable",
    "Histogram":    "Histogram",
}


class NodeRegistry:
    def __init__(self):
        self.links       = {}   # link_id -> (start_slot, end_slot)
        self.connections = {}   # start_slot -> end_slot
        self.nodes       = {}   # node_id (int) -> node_type (str)
        self.node_names  = {}   # node_id (int) -> custom name (str)
        self.slot_node   = {}   # slot tag / UUID -> node_id
        self.next_id     = 0


REGISTRY = NodeRegistry()

EXTENDED_FONT = None  # Set by fce.py; used for Dingbats icon rendering in node buttons
LARGE_FONT = None     # Set by fce.py; used for tutorial title and Run button


def update_run_state(key, value):
    with STATE_LOCK:
        RUN_STATE[key] = value


def get_run_state(key):
    with STATE_LOCK:
        return RUN_STATE[key]


safe_set_state = update_run_state
safe_get_state = get_run_state


def add_active_node(nid: int):
    """Mark a node as currently being processed (thread-safe)."""
    with STATE_LOCK:
        RUN_STATE["active_nodes"] = RUN_STATE["active_nodes"] | {nid}


def add_completed_node(nid: int):
    """Move a node from active → completed (thread-safe)."""
    with STATE_LOCK:
        RUN_STATE["active_nodes"]    = RUN_STATE["active_nodes"] - {nid}
        RUN_STATE["completed_nodes"] = RUN_STATE["completed_nodes"] | {nid}


def mark_nodes_completed(nids):
    """Atomically mark a collection of node IDs as completed."""
    nid_set = set(nids)
    with STATE_LOCK:
        RUN_STATE["active_nodes"]    = RUN_STATE["active_nodes"] - nid_set
        RUN_STATE["completed_nodes"] = RUN_STATE["completed_nodes"] | nid_set


# ── download state ────────────────────────────────────────────────────────────
DOWNLOAD_LOG_QUEUE = queue.Queue()
_DOWNLOAD_RUNNING  = False
_DOWNLOAD_LOCK     = threading.Lock()


def set_download_running(val: bool):
    global _DOWNLOAD_RUNNING
    with _DOWNLOAD_LOCK:
        _DOWNLOAD_RUNNING = val


def get_download_running() -> bool:
    with _DOWNLOAD_LOCK:
        return _DOWNLOAD_RUNNING
