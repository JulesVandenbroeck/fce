import os
import uproot


def write_final_histograms(hdir, s, h5, outHist, out_path=None):
    """Write histogram to out_path and to the permanent h5-keyed cache file."""
    if out_path is None:
        out_path = os.path.join(hdir, "output", f"{s}.root")
    with uproot.recreate(out_path) as f:
        f["h"] = outHist.h["h"]
    cache_path = os.path.join(hdir, "output", f"h5_{h5}_{s}.root")
    with uproot.recreate(cache_path) as f:
        f["h"] = outHist.h["h"]
