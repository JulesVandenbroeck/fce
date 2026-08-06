import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplhep as hep
from paths import get_fce_home

plt.style.use(hep.style.ROOT)


def generate_cutflow_plot(cfg, active_samples, header_cache, selections):
    """Normalized stacked bar cut-flow chart saved as PNG. Returns path or ''."""
    import json
    hdir = get_fce_home()
    try:
        # Load samples.json to get the canonical sample order (matches plotter.py)
        _config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "samples.json"
        )
        with open(_config_path) as _f:
            _samples_json = json.load(_f)
        en = cfg.get("energy", "").replace(" GeV", "")
        _sample_order = list(_samples_json.get(en, {}).keys())

        mc_all = [s for s in active_samples if s != "data"]
        # Sort mc_samples to match the canonical order from samples.json
        mc_samples = [s for s in _sample_order if s in mc_all]
        mc_samples += [s for s in mc_all if s not in mc_samples]

        if not mc_samples:
            return ""

        # Stage 0: total before cuts
        stages = [("Total", {s: header_cache.get(s, 0) for s in mc_samples})]
        total_raw_all = sum(header_cache.get(s, 0) for s in active_samples)

        for sel_cfg in selections:
            h5_sel = sel_cfg["h5_sel"]
            sel_name = (sel_cfg.get("node_name") or "").strip() or "Selection"
            per_sample = {}
            for s in mc_samples:
                cache_path = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")
                if os.path.exists(cache_path):
                    try:
                        d = np.load(cache_path, mmap_mode="r")
                        per_sample[s] = len(d["weight"])
                    except Exception:
                        per_sample[s] = 0
                else:
                    per_sample[s] = 0
            stages.append((sel_name, per_sample))

        n_stages = len(stages)
        n_mc = len(mc_samples)
        x_pos = np.arange(n_stages)

        counts = np.zeros((n_stages, n_mc), dtype=float)
        for i, (_, per_s) in enumerate(stages):
            for j, s in enumerate(mc_samples):
                counts[i, j] = per_s.get(s, 0)

        stage_mc_totals = counts.sum(axis=1)
        fractions = np.where(
            stage_mc_totals[:, None] > 0,
            counts / stage_mc_totals[:, None] * 100.0,
            0.0,
        )

        # Efficiency: all active samples / total_raw_all (matches existing cutflow logic)
        efficiencies = [100.0]
        for sel_cfg in selections:
            h5_sel = sel_cfg["h5_sel"]
            n_pass = 0
            for s in active_samples:
                cache_path = os.path.join(hdir, "cache", f"sel_{h5_sel}_{s}.npz")
                if os.path.exists(cache_path):
                    try:
                        d = np.load(cache_path, mmap_mode="r")
                        n_pass += len(d["weight"])
                    except Exception:
                        pass
            efficiencies.append(100.0 * n_pass / total_raw_all if total_raw_all > 0 else 0.0)

        fig_w = max(6.36, 1.5 * n_stages + 2.0)
        fig, ax = plt.subplots(figsize=(fig_w, 5.5), dpi=200)

        cmap = matplotlib.colormaps["tab10"].resampled(n_mc)
        bottoms = np.zeros(n_stages)

        proc_map = {}
        for sel_cfg in selections:
            for hcfg in sel_cfg.get("histograms", []):
                proc_map.update(hcfg.get("process_names_map", {}))

        for j, s in enumerate(mc_samples):
            ax.bar(x_pos, fractions[:, j], bottom=bottoms,
                   color=cmap(j), label=proc_map.get(s, s),
                   edgecolor="black", linewidth=0.8, alpha=0.85, width=0.6)
            bottoms += fractions[:, j]

        for i, eff in enumerate(efficiencies):
            ax.text(x_pos[i], bottoms[i] + 1.5, f"{eff:.1f}%",
                    ha="center", va="bottom", fontsize=10,
                    color="black", fontweight="bold")

        ax.set_xticks(x_pos)
        ax.set_xticklabels([s[0] for s in stages], rotation=45, ha="right", fontsize=11)
        ax.set_ylabel("MC Composition (%)", fontsize=14)
        ax.set_ylim(0, 115)
        ax.tick_params(axis="y", labelsize=11)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0,
                  frameon=False, fontsize=11)

        detector = cfg.get("detector", "")
        energy = cfg.get("energy", "")
        ax.text(0.0, 1.02, "FCE", transform=ax.transAxes,
                ha="left", va="bottom", fontsize=14, fontweight="bold")
        ax.text(1.0, 1.02, f"{detector}, √s = {energy}",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=14)

        fig.tight_layout(pad=1.5)
        out_path = os.path.join(hdir, "cutflow.png")
        plt.savefig(out_path, format="png", bbox_inches="tight")
        plt.close(fig)
        return out_path

    except Exception:
        return ""
