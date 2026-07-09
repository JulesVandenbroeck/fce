# Future Collider Experiment Studio

## Prompt
You are working on a learning tool for particle physics data analysis at The Future Collider Experiment. The goal is to implement UI based changes suggested by the user. The tool is based on a drag and drop coding structure in python using DearPyGui framework. The framework exists out of building blocks that can be connected to create a pipeline of data analysis strategy. The building blocks of the tool are Data, Multiplicity, Selection, Histogram, and Observable (with four subtypes: Global, Object, Vector Sum, Custom).

## Rules
- The main github branch is `main` and remote is `juvanden` (git@github.com:JulesVandenbroeck/fce.git). 
- Feature requests are done trough a prompt in @prompt.md.
- For each request to add or change a feature of the learning tool by the user the following steps need to be done in order:
    1) A new github branch is created for the feature. In case of small changes multiple features can be bundled into 1 branch.
    2) you develop the code for the feature
    3) Default tests are run on the feature. If all tests pass the changes are commited and the feature is ready for review.
    4) The feature is summarized in a pull request to main by you after which it is reviewed by the user. If modifications are requested by the user, go back to step 2.
    5) The pull request can only be merged by the user after review.
    6) Update the documentation in CLAUDE.md if applicable and place the feature prompt from prompt.md to a history.md file in .claude.
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
|   |-- components.py    # UI callbacks: trigger_analysis_pipeline(), dataset download, canvas refresh
|   `-- tutorial.py      # 11-page launch tutorial: show_tutorial(), page navigation, item highlighting
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
| 3     | ObsGlobal   | Observable   | Event-level count observable (nlep, nel, nmu, njets, nphot). `+` button sums multiple terms; `x` removes a term. |
| 3     | ObsObject   | Observable   | Per-object property (object combo + variable combo, filtered per type). `+` sums terms; `x` removes. Builds e.g. `l1.pt` or `l1.pt + l2.pt`. |
| 3     | ObsVectorSum | Observable  | Combined 4-vector property. Result combo (mass/pt/eta/phi/e) + 2+ object rows. `+` adds objects; `x` removes when >2. Builds e.g. `(l1.p4 + l2.p4).mass`. |
| 3     | ObsCustom   | Observable   | Free-text expression with autocomplete and `?` help (same as original Observable). |
| 3     | Observable  | Observable   | Legacy free-text node (kept for undo compatibility). |
| 4     | Histogram   | Histogram    | Sets bins, range min/max, and optional signal for statistical fit. Multiple Histogram nodes can connect to one Observable node, each producing a separate plot. Custom node name is used as the collapsing header label in multi-plot display. |

Multiplicity and Selection nodes can be chained to themselves (AND logic). Multiple Observable nodes may share one Selection (fan-out). Multiple Histogram nodes may share one Observable (fan-out). Only one DataSource is allowed.

Multiple Selection nodes may attach to the same Multiplicity node (independent branches). Each branch gets its own selection-level `.npz` cache keyed by its expression set. When Selection_B is AND-chained from Selection_A and Selection_A also fans out to an Observable, Selection_B's cache is derived from Selection_A's cache (filtered with the additional expression) rather than re-reading ROOT.

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
   - `run_physics_loop()` iterates ROOT files with `uproot`, applying cuts via `filter_raw_event_data()`. Selection expressions are pre-compiled with `compile()` once per selection branch and passed as code objects, avoiding repeated AST parsing per event. Samples within a selection branch are processed in parallel via `ThreadPoolExecutor` (up to 4 workers); each sample writes to a unique cache path so workers never conflict.
   - Two-level cache: selection-level `.npz` cache (accumulated with pre-allocated numpy float32 arrays, ~9× less RAM than Python lists) and histogram-level `.root` cache avoid re-processing. Cache arrays are loaded with `mmap_mode='r'` so the OS pages in only the columns accessed by the observable expression.
   - `render_plots()` produces one PNG per histogram config (`hist_{i}.png`), loaded into separate DPG texture buffers. A single selection with one histogram shows as a full-width image; multiple selections each appear under a collapsing header labelled by the Selection node's custom name (open by default). Within a selection, multiple histograms are stacked without an inner dropdown.
   - Optional `run_fit()` computes signal strength mu and significance via `pyhf`.
7. `_frame_poll_callback()` polls state every 6 frames, updating the progress bar and canvas.

## Observable node internals

- `_is_obs(ntype)` — predicate covering all five obs types; used throughout `graph.py` in place of `== "Observable"`.
- `obs_expr_{nid}` — hidden `input_text` storing the built expression for typed nodes; read by `compile_graph_topology`. `ObsCustom`/legacy `Observable` still use `txt_obs_{nid}`.
- `_build_obs_expr(nid, subtype)` — reads combo values, builds the expression string, writes to `obs_expr_{nid}`.
- `_build_obs_label(nid, subtype)` — returns a LaTeX-formatted x-axis label (e.g. `$p_T(l_1)$ [GeV]`) stored as `x_label` in `hcfg`; used by `engine/plotter.py`.
- `_OBS_ROW_COUNT: dict[int,int]` — tracks number of term rows per node.
- Row management uses rebuild functions (`_obs_rebuild_global_rows`, `_obs_rebuild_object_rows`, `_obs_rebuild_vecsum_rows`) that delete all row-group children and recreate from a values list. Both add (`+` button) and delete (`x` button) use these rebuilds. `x` button only shown when row count exceeds the minimum (1 for Global/Object, 2 for VectorSum).
- Snapshots include `obs_row_count`; `_restore_node` calls the rebuild function with saved values before the generic `set_value` pass so Ctrl+Z restores the full row state.

## Key state

- `ui/state.py:REGISTRY` -- NodeRegistry tracking all node ids, types, links, slot mappings, and custom names (`node_names: dict[int, str]`).
- `ui/state.py:RUN_STATE` -- thread-safe dict: `progress`, `running`, `stop`, `status_msg`, `fit_mu`, `fit_sig`.
- FCE home directory (`~/.fce` or temp fallback): stores `cache/`, `output/`, `datasets/`.

## UI interactions

- **Canvas navigation**: Right-click + drag pans all nodes. Scroll wheel pans vertically (Shift+scroll pans horizontally).
- **Node/link deletion**: Select a node or link, then press Backspace or Delete. Blocked while any input field is active. The × button in the node name row also deletes.
- **Undo (Ctrl+Z)**: Restores the last deleted node or link. History depth is 10. Node undo also restores connected links and all widget values.
- **Right-click on a link**: Deletes the hovered link (short click, < 5 px movement).
- **Initial nodes**: Created pre-connected (DataSource → Multiplicity → Selection → Observable → Histogram) with named labels. `create_node()` accepts an optional `name` parameter.
- **Node palette**: A fixed 70 px bar at the bottom lists draggable node templates. Multiplicity, Selection, and Histogram are dragged directly. The Observable button opens a submenu (toggled via `palette_main_grp`/`palette_obs_grp` visibility) showing four draggable subtypes (Global, Object, Vec Sum, Custom) and a `< Back` button. Drop position is computed via `dpg.get_item_pos("node_editor_pane")` (screen-space origin) subtracted from `dpg.get_mouse_pos(local=False)`. Toggle functions `_show_obs_submenu` and `_show_main_palette` are defined in `fce.py`.
- **Save/Load pipeline**: File menu → Save Pipeline... / Load Pipeline... opens a native file dialog. Save serialises all nodes (positions, types, widget values, Observable row counts) and links to a JSON file. Load clears the canvas, recreates all nodes with saved state (calls Observable row-rebuild functions), then reconnects links in dependency order. Implemented in `fce.py` (`_save_pipeline`, `_load_pipeline`) and `ui/graph.py` (`save_pipeline_json`, `load_pipeline_json`).
- **Launch tutorial**: `ui/tutorial.py:show_tutorial()` is called via `dpg.set_frame_callback(frame=1, ...)` and shows a non-modal 640×310 px popup with 11 pages covering every feature. Navigation: `< Prev` (disabled on page 1), `Skip`, `Next >` (becomes `Finish` on the last page). Each page that references a specific UI element applies a gold DPG theme to it (`_tut_node_hl` for nodes, `_tut_item_hl` for buttons/headers/child-windows); highlight is cleared on page change, Skip, Finish, or X-close. All text is ASCII-only (DPG's default glyph range is Latin-1; characters above U+00FF render as `?`). Title+body live in a `child_window(height=-40)` so the nav bar is always pinned to the bottom. `ui/state.py:LARGE_FONT` holds the 20 px font used for the tutorial title.

## Testing approach

The UI layer (`fce.py`, `ui/graph.py`, `ui/components.py`) requires a DPG display and cannot be tested headlessly. All other modules are headless-safe:

- `paths.py` -- home directory resolution and cache environment setup
- `ui/state.py` -- NodeRegistry and thread-safe state accessors
- `engine/path_filter.py` -- physics object proxies, delta-R, expression eval, cache I/O
- `objects.py` -- physics object constructors

Tests live in `tests/` and are run with `pytest`. Linting uses `flake8` (config in `.flake8`).
