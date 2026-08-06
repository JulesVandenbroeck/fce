from paths import get_fce_home
import uproot
import mplhep as hep
import matplotlib.pyplot as plt
import os
import warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")

from engine.systematics import LUMI_UNC, SYST_SOURCES


hdir = get_fce_home()
plt.style.use(hep.style.ROOT)


def render_plots(cfg, samples, en):
    detector = cfg["detector"]
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
    x_label = hcfg.get("x_label", observable_target)
    h_mc, s_mc, h_data = [], [], None

    # Per-source MC variation accumulators: {src: accumulated_up_counts or None}
    mc_up = {src: None for src in SYST_SOURCES}

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
                    vals = h_obj.values()
                    edges = h_obj.axes[0].edges()

                    if s != "data":
                        # Read available variation templates for this MC sample.
                        for src in SYST_SOURCES:
                            key = f"h_{src}_up"
                            if key in f_res:
                                up_vals = f_res[key].values()
                                if mc_up[src] is None:
                                    mc_up[src] = np.array(up_vals, dtype=float)
                                else:
                                    mc_up[src] = mc_up[src] + np.array(up_vals, dtype=float)

                if s != "data":
                    s_mc.append(s)
                    h_mc.append((vals, edges))
                else:
                    h_data = (vals, edges)
            except Exception:
                continue

    has_ratio = h_data is not None and bool(h_mc)

    if has_ratio:
        fig, (ax, ax_ratio) = plt.subplots(
            2, 1, figsize=(6.36, 5.5), dpi=200,
            gridspec_kw={"height_ratios": [3, 1], "hspace": 0}, sharex=True,
        )
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
    else:
        fig, ax = plt.subplots(figsize=(6.36, 4.54), dpi=200)
        ax_ratio = None

    # Compute mc_stack early so it is available for both the band and the ratio panel.
    mc_stack = None
    frac = None
    if h_mc:
        mc_vals = [v for v, _ in h_mc]
        mc_edges = h_mc[0][1]
        mc_stack = np.sum([np.array(v, dtype=float) for v in mc_vals], axis=0)
        cmap = matplotlib.colormaps["tab10"].resampled(len(h_mc))
        mc_colors = [cmap(i) for i in range(len(h_mc))]
        proc_map = hcfg.get("process_names_map", {})
        mc_labels = [proc_map.get(s, s) for s in s_mc]
        hep.histplot(
            mc_vals, mc_edges, label=mc_labels, stack=True, color=mc_colors,
            histtype="fill", edgecolor="black", linewidth=1.2, alpha=0.8, ax=ax,
        )

        # ── Systematic uncertainty band (main panel) ─────────────────────
        # Compute per-bin fractional total systematic in quadrature.
        # Guard bins where mc_stack == 0 to avoid divide-by-zero.
        centers = 0.5 * (mc_edges[:-1] + mc_edges[1:])
        safe_stack = np.where(mc_stack > 0, mc_stack, 1.0)
        frac2 = np.full(len(mc_stack), LUMI_UNC ** 2)
        for src in SYST_SOURCES:
            if mc_up[src] is not None and len(mc_up[src]) == len(mc_stack):
                src_frac = (mc_up[src] - mc_stack) / safe_stack
                frac2 = frac2 + np.where(mc_stack > 0, src_frac ** 2, 0.0)
        frac = np.sqrt(frac2)
        frac = np.where(mc_stack > 0, frac, 0.0)
        band = mc_stack * frac

        ax.fill_between(
            np.repeat(mc_edges, 2)[1:-1],
            np.repeat(mc_stack - band, 2),
            np.repeat(mc_stack + band, 2),
            facecolor="none", hatch="///", edgecolor="grey",
            linewidth=0.0, label="Syst. unc.",
        )

    if h_data is not None:
        d_vals, d_edges = h_data
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning,
                                    message=".*sumw are zero.*")
            # w2=d_vals gives Poisson (sqrt-N) error bars per bin
            hep.histplot(
                d_vals, d_edges, w2=d_vals, label="Pseudo-data",
                histtype="errorbar", color="black", marker="o", markersize=4, ax=ax,
            )

    ax.text(0.0, 1.02, "FCE", transform=ax.transAxes,
            ha="left", va="bottom", fontsize=14, fontweight="bold")
    l_txt = f"{detector}, √s = {cfg['energy']}"
    ax.text(1.0, 1.02, l_txt, transform=ax.transAxes, ha="right", va="bottom", fontsize=14)

    ax.tick_params(axis="both", labelsize=11)
    if not has_ratio:
        ax.set_xlabel(x_label, fontsize=14)
    ax.set_ylabel("Events / Bin", fontsize=14)
    ax.legend(loc="upper right", frameon=True, fontsize=12)

    process_name = hcfg.get("process_name")
    if process_name:
        ax.text(0.02, 0.97, f"Discovered: {process_name}",
                transform=ax.transAxes, ha="left", va="top", fontsize=11,
                color="white", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#2a7a2a",
                          alpha=0.85, edgecolor="white", linewidth=0.5))

    if has_ratio:
        d_arr = np.array(h_data[0], dtype=float)
        empty = (mc_stack == 0) | (d_arr == 0)
        ratio = np.where(empty, 1.0, d_arr / np.where(mc_stack == 0, 1.0, mc_stack))
        ratio_err = np.where(empty, 0.0,
                             np.sqrt(np.maximum(d_arr, 0.0)) /
                             np.where(mc_stack == 0, 1.0, mc_stack))
        edges_r = h_mc[0][1]
        centers = 0.5 * (edges_r[:-1] + edges_r[1:])
        widths = edges_r[1:] - edges_r[:-1]

        ax_ratio.errorbar(
            centers, ratio, yerr=ratio_err, xerr=widths / 2,
            fmt="o", color="black", markersize=4, linewidth=1.0,
        )
        ax_ratio.axhline(1.0, color="gray", linewidth=1.0, linestyle="--")
        ax_ratio.set_ylim(0.0, 2.0)
        ax_ratio.set_xlabel(x_label, fontsize=14)
        ax_ratio.set_ylabel("Data / Pred.", fontsize=12)
        ax_ratio.tick_params(axis="both", labelsize=10)

        # ── Systematic uncertainty band (ratio panel) ─────────────────────
        if mc_stack is not None and frac is not None:
            ratio_edges = edges_r
            ax_ratio.fill_between(
                np.repeat(ratio_edges, 2)[1:-1],
                np.repeat(1.0 - frac, 2),
                np.repeat(1.0 + frac, 2),
                facecolor="none", hatch="///", edgecolor="grey", linewidth=0.0,
            )

    fig.tight_layout(pad=1.5)
    plt.savefig(os.path.join(hdir, f"hist_{hist_idx}.png"),
                dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
