import os
import uproot


def write_final_histograms(hdir, s, cfg, outHist):
    """Writes the finalized analytical calculations into physics output ROOT structures."""
    with uproot.recreate(os.path.join(hdir, "output", f"{s}.root")) as out:
        out["h"] = outHist.h["h"]
    with uproot.recreate(os.path.join(hdir, "output", f"h5_{cfg['h5']}_{s}.root")) as out:
        out["h"] = outHist.h["h"]
