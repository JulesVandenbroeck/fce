"""Physics-motivated systematic uncertainty constants and helpers.

Imported by engine.path_filter, engine.fitter, and engine.plotter so that
every module shares a single source of truth for the uncertainty magnitudes.
"""

LUMI_UNC = 0.025       # 2.5% correlated luminosity uncertainty
JEC_PER_JET = 0.015    # 1.5% per jet (Jet Energy Correction)
LEP_PER_EL = 0.01      # 1.0% per electron
LEP_PER_MU = 0.005     # 0.5% per muon
BTAG_PER_BJET = 0.02   # 2.0% per b-tagged jet
BTAG_WP = 0.7          # b-tagging working point (btag score threshold)

# Object/weight-based systematic sources (order defines histogram keys h_{src}_up)
SYST_SOURCES = ("jec", "lep", "btag")


def event_syst_factor(source, njets, nel, nmu, nbjets):
    """Return the per-event UP weight multiplier for the given systematic source.

    Works on both scalars and numpy arrays (plain arithmetic vectorises).

    Parameters
    ----------
    source : str
        One of 'jec', 'lep', 'btag'.
    njets : int or array-like
        Number of jets in the event.
    nel : int or array-like
        Number of electrons in the event.
    nmu : int or array-like
        Number of muons in the event.
    nbjets : int or array-like
        Number of b-tagged jets in the event (btag score > BTAG_WP).

    Returns
    -------
    float or numpy array
        Multiplicative weight factor for the UP variation (always >= 1.0 for
        typical event multiplicities).
    """
    if source == "jec":
        return 1.0 + JEC_PER_JET * njets
    if source == "lep":
        return 1.0 + LEP_PER_EL * nel + LEP_PER_MU * nmu
    if source == "btag":
        return 1.0 + BTAG_PER_BJET * nbjets
    raise ValueError(f"Unknown systematic source: {source!r}")
