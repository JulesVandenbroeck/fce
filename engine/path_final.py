import os
import uproot


def write_final_histograms(hdir, s, h5, outHist, out_path=None):
    """Write histogram to out_path and to the permanent h5-keyed cache file.

    All keys in outHist.h are written (nominal 'h' plus any variation templates
    such as 'h_jec_up', 'h_lep_up', 'h_btag_up') so the h5 cache carries them
    through automatically without any cache-key changes.
    """
    if out_path is None:
        out_path = os.path.join(hdir, "output", f"{s}.root")
    with uproot.recreate(out_path) as f:
        for k, hobj in outHist.h.items():
            f[k] = hobj
    cache_path = os.path.join(hdir, "output", f"h5_{h5}_{s}.root")
    with uproot.recreate(cache_path) as f:
        for k, hobj in outHist.h.items():
            f[k] = hobj
