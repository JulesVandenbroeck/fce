# Future Collider Experiment Studio

## Prompt
You are working on a learning tool for particle physics data analysis at The Future Collider Experiment. The goal is to implement UI based changes suggested by the user. The tool is based on a drag and drop coding structure in python using DearPyGui framework. The framework exists out of building blocks that can be connected to create a pipeline of data analysis strategy. The building blocks of the tool are Data, Multiplicity, Selection, Histogram, Observable. 

## Rules
- The main github branch is `main` and remote is `juvanden` (git@github.com:JulesVandenbroeck/fce.git).
- For each request to add or change a feature of the learning tool  by the user the following steps need to be done in order:
    1) A new github branch is created for the feature. In case of small changes multiple features can be bundled into 1 branch.
    2) you develop the code for the feature
    3) Default tests are run on the feature. If all tests pass the chnges are commited and the feature is ready for review.
    2) The feature is summarized in a pull request to main by you after which it is reviewed by the user. If modifications are requested by the user, go back to step 2.
    4) The pull request can only be merged by the user after review.
- In case of uncertainty in the implementation or UI of a feature always ask the user to resolve. Never make assumptions, instead give options to the user.
- Run all `git` commands with `rtk` in front if this is not done by default
- Always open pull requests against the `juvanden` remote (JulesVandenbroeck/fce). Use `gh pr create --repo JulesVandenbroeck/fce`.

## context
tool github: https://github.com/JulesVandenbroeck/fce (remote: juvanden)
DearPyGui framework: https://github.com/hoffstadt/DearPyGui

## Repository structure

```
fce/
|-- fce.py               # Entry point: creates DPG context, all windows, and initial nodes
|-- __main__.py          # Package entry point (calls fce.py)
|-- __init__.py          # Package version (__version__)
|-- objects.py           # Physics object classes: event, lepton, jet, photon, MET
|-- paths.py             # FCE home directory resolution (writable cache/output/datasets)
|-- run_engine.py        # Analysis orchestrator: execute_analysis(), run_dataset_download()
|-- ui/
|   |-- state.py         # Shared state: NodeRegistry, RUN_STATE, NODE_HIERARCHY, download queue
|   |-- graph.py         # Node creation, link/delink callbacks, topology compiler, error highlighting
|   `-- components.py    # UI callbacks: trigger_analysis_pipeline(), dataset download, canvas refresh
|-- engine/
|   |-- analytical_loop.py  # Event loop: reads ROOT files, applies cuts, fills histograms
|   |-- path_filter.py      # Per-event and vectorized filtering, expression eval, cache I/O
|   |-- path_final.py       # Writes final per-sample ROOT histogram files
|   |-- plotter.py          # Renders matplotlib plot to PNG -> DPG texture
|   |-- fitter.py           # pyhf-based statistical fit (signal strength mu, discovery significance)
|   `-- downloader.py       # Downloads ROOT datasets from remote
`-- config/
    |-- samples.json     # Sample definitions per energy: {energy: {sample_name: {label, color, ...}}}
    |-- analysis.dat     # (legacy)
    `-- selection.dat    # (legacy)
```

## Node types and hierarchy

Nodes are connected left-to-right in a strict hierarchy enforced by `NODE_HIERARCHY` in `ui/state.py`:

| Level | Node type    | DPG label    | Description |
|-------|-------------|--------------|-------------|
| 0     | DataSource  | Data         | Selects energy (91/160/240/365 GeV) and detector (IDEA/CLD). Exactly one per graph. |
| 1     | Multiplicity | Multiplicity | Minimum object counts: leptons (Any/Electron/Muon), jets, photons. Chainable (AND logic). |
| 2     | Selection   | Selection    | Free-form boolean expression over physics variables. Chainable (AND logic). |
| 3     | Observable  | Observable   | Arithmetic expression over physics objects for histogramming (e.g. `met.pt`). |
| 4     | Histogram   | Histogram    | Sets bins, range min/max, and optional signal for statistical fit. |

Multiplicity and Selection nodes can be chained to themselves (AND logic). All other connections must go from lower to higher level. Only one DataSource is allowed.

## Physics variables (expressions)

Available in Selection and Observable nodes:

- **Counts**: `nlep`, `nel`, `nmu`, `njets`, `nphot`
- **Leptons** (pt-sorted): `l1.pt`, `l1.eta`, `l1.phi`, `l1.e`, `l1.d0`, `l1.z0`, `l1.p4` (and `l2.*`)
- **Jets**: `j1.pt`, `j1.eta`, `j1.phi`, `j1.e`, `j1.btag`, `j1.p4` (and `j2.*`)
- **Photons**: `ph1.pt`, `ph1.eta`, `ph1.phi`, `ph1.e`, `ph1.p4` (and `ph2.*`)
- **MET**: `met.pt`, `met.eta`, `met.phi`, `met.e`, `met.p4`
- **4-vector arithmetic**: `(l1.p4 + l2.p4).mass`, `(l1.p4 + l2.p4).pt`, `l1.p4.deltaR(l2.p4)`, `deltaR(l1, l2)`

## Data flow

1. `fce.py` creates the DPG viewport and initial nodes; `setup_link_handlers()` registers mouse callbacks.
2. User connects nodes in the node editor to build a pipeline.
3. "Run" button calls `trigger_analysis_pipeline()` in `ui/components.py`.
4. Pipeline connectivity and expression syntax are validated; errors highlight nodes red.
5. `compile_graph_topology()` serializes the node graph to a `cfg` dict.
6. `execute_analysis(cfg)` runs in a background thread:
   - Reads `config/samples.json` for the active sample list at the selected energy.
   - `run_physics_loop()` iterates ROOT files with `uproot`, applying cuts via `filter_raw_event_data()`.
   - Two-level cache: selection-level `.npz` cache and histogram-level `.root` cache avoid re-processing.
   - `render_plots()` produces a matplotlib PNG loaded into the DPG texture buffer.
   - Optional `run_fit()` computes signal strength mu and significance via `pyhf`.
7. `_frame_poll_callback()` polls state every 6 frames, updating the progress bar and canvas.

## Key state

- `ui/state.py:REGISTRY` -- NodeRegistry tracking all node ids, types, links, and slot mappings.
- `ui/state.py:RUN_STATE` -- thread-safe dict: `progress`, `running`, `stop`, `status_msg`, `fit_mu`, `fit_sig`.
- FCE home directory (`~/.fce` or temp fallback): stores `cache/`, `output/`, `datasets/`.

## Testing approach

The UI layer (`fce.py`, `ui/graph.py`, `ui/components.py`) requires a DPG display and cannot be tested headlessly. All other modules are headless-safe:

- `paths.py` -- home directory resolution and cache environment setup
- `ui/state.py` -- NodeRegistry and thread-safe state accessors
- `engine/path_filter.py` -- physics object proxies, delta-R, expression eval, cache I/O
- `objects.py` -- physics object constructors

Tests live in `tests/` and are run with `pytest`. Linting uses `flake8` (config in `.flake8`).
