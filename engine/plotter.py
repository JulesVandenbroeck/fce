import os
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplhep as hep
import uproot

from paths import get_fce_home

hdir = get_fce_home()
plt.style.use(hep.style.ROOT)


def render_plots(cfg, samples, en):
    detector   = cfg["detector"]
    selections = cfg.get("selections")

    if selections:
        for sel_cfg in selections:
            for hcfg in sel_cfg["histograms"]:
                _render_single(cfg, samples, en, hcfg["plot_idx"], hcfg, detector)
    else:
        # Fallback: flat histograms list
        histograms = cfg.get("histograms", [{
            "observable": cfg["observable"],
            "bins": cfg["bins"], "min": cfg["min"], "max": cfg["max"],
            "target": cfg["target"], "h5": cfg["h5"],
        }])
        for i, hcfg in enumerate(histograms):
            _render_single(cfg, samples, en, hcfg.get("plot_idx", i), hcfg, detector)


def _render_single(cfg, samples, en, hist_idx, hcfg, detector):
    observable_target = hcfg["observable"]
    h_mc, s_mc, h_data = [], [], None

    if en in samples:
        for s in samples[en].keys():
            root_out = os.path.join(hdir, "output", f"hist{hist_idx}_{s}.root")
            if not os.path.exists(root_out):
                continue
            try:
                with uproot.open(root_out) as f_res:
                    if "h" not in f_res:
                        continue
                    h_obj = f_res["h"]
                    vals  = h_obj.values()
                    edges = h_obj.axes[0].edges()
                if s != "data":
                    s_mc.append(s)
                    h_mc.append((vals, edges))
                else:
                    h_data = (vals, edges)
            except Exception:
                continue

    fig, ax = plt.subplots(figsize=(6.36, 4.54), dpi=200)

    if h_mc:
        mc_vals  = [v for v, _ in h_mc]
        mc_edges = h_mc[0][1]
        cmap      = matplotlib.colormaps["tab10"].resampled(len(h_mc))
        mc_colors = [cmap(i) for i in range(len(h_mc))]
        hep.histplot(
            mc_vals, mc_edges, label=s_mc, stack=True, color=mc_colors,
            histtype="fill", edgecolor="black", linewidth=1.2, alpha=0.8, ax=ax,
        )

    if h_data is not None:
        d_vals, d_edges = h_data
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning,
                                    message=".*sumw are zero.*")
            hep.histplot(
                d_vals, d_edges, label="Data Simulation",
                histtype="errorbar", color="black", marker="o", markersize=4, ax=ax,
            )

    ax.text(0.0, 1.02, "FCE", transform=ax.transAxes,
            ha="left", va="bottom", fontsize=14, fontweight="bold")
    l_txt = f"{detector}, $\\sqrt{{s}}$ = {cfg['energy']}"
    ax.text(1.0, 1.02, l_txt, transform=ax.transAxes, ha="right", va="bottom", fontsize=14)

    ax.tick_params(axis="both", labelsize=11)
    ax.set_xlabel(observable_target, fontsize=14)
    ax.set_ylabel("Events / Bin", fontsize=14)
    ax.legend(loc="upper right", frameon=True, fontsize=12)

    fig.tight_layout(pad=1.5)
    plt.savefig(os.path.join(hdir, f"hist_{hist_idx}.png"),
                dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
