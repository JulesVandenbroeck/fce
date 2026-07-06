import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.state import (
    NodeRegistry, NODE_HIERARCHY, NODE_LABELS,
    get_run_state, update_run_state,
    get_download_running, set_download_running,
)


def test_node_hierarchy_order():
    assert NODE_HIERARCHY["DataSource"] < NODE_HIERARCHY["Multiplicity"]
    assert NODE_HIERARCHY["Multiplicity"] < NODE_HIERARCHY["Selection"]
    assert NODE_HIERARCHY["Selection"] < NODE_HIERARCHY["Observable"]
    assert NODE_HIERARCHY["Observable"] < NODE_HIERARCHY["Histogram"]


def test_node_labels_defined():
    for node_type in ("DataSource", "Multiplicity", "Selection", "Observable", "Histogram"):
        assert node_type in NODE_LABELS
        assert isinstance(NODE_LABELS[node_type], str)


def test_node_registry_starts_empty():
    reg = NodeRegistry()
    assert reg.nodes == {}
    assert reg.links == {}
    assert reg.connections == {}
    assert reg.slot_node == {}
    assert reg.next_id == 0


def test_node_registry_tracks_nodes():
    reg = NodeRegistry()
    reg.nodes[0] = "DataSource"
    reg.nodes[1] = "Selection"
    assert len(reg.nodes) == 2
    assert reg.nodes[0] == "DataSource"


def test_run_state_get_set():
    original = get_run_state("running")
    update_run_state("running", True)
    assert get_run_state("running") is True
    update_run_state("running", original)


def test_run_state_thread_safety():
    results = []

    def worker():
        for _ in range(100):
            update_run_state("progress", 0.5)
            results.append(get_run_state("progress"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == 0.5 for r in results)
    update_run_state("progress", 0.0)


def test_download_running_flag():
    set_download_running(False)
    assert get_download_running() is False
    set_download_running(True)
    assert get_download_running() is True
    set_download_running(False)
