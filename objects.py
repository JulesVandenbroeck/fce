import vector


class event:

    def __init__(self, ev, i):
        self.w = ev["weight"][i]


class lepton:

    def __init__(self, ev, i, idx, typ):
        self.idx = idx
        self.typ = typ

        if typ == 0:
            self.pt = ev["electron_pt"][i][idx]
            self.eta = ev["electron_eta"][i][idx]
            self.phi = ev["electron_phi"][i][idx]
            self.e = ev["electron_e"][i][idx]
            self.d0 = ev["electron_d0signif"][i][idx]
            self.z0 = ev["electron_z0signif"][i][idx]
        else:
            self.pt = ev["muon_pt"][i][idx]
            self.eta = ev["muon_eta"][i][idx]
            self.phi = ev["muon_phi"][i][idx]
            self.e = ev["muon_e"][i][idx]
            self.d0 = ev["muon_d0signif"][i][idx]
            self.z0 = ev["muon_z0signif"][i][idx]

        self.p4 = vector.obj(
            pt=self.pt,
            eta=self.eta,
            phi=self.phi,
            e=self.e,
        )


class jet:

    def __init__(self, ev, i, idx):
        self.idx = idx
        self.pt = ev["jet_pt"][i][idx]
        self.eta = ev["jet_eta"][i][idx]
        self.phi = ev["jet_phi"][i][idx]
        self.e = ev["jet_e"][i][idx]
        self.btag = ev["jet_btag"][i][idx]
        self.p4 = vector.obj(
            pt=self.pt,
            eta=self.eta,
            phi=self.phi,
            e=self.e,
        )


class photon:

    def __init__(self, ev, i, idx):
        self.idx = idx
        self.pt = ev["photon_pt"][i][idx]
        self.eta = ev["photon_eta"][i][idx]
        self.phi = ev["photon_phi"][i][idx]
        self.e = ev["photon_e"][i][idx]
        self.p4 = vector.obj(
            pt=self.pt,
            eta=self.eta,
            phi=self.phi,
            e=self.e,
        )


class MET:

    def __init__(self, ev, i):
        self.pt = ev["MET_pt"][i]
        self.eta = ev["MET_eta"][i]
        self.phi = ev["MET_phi"][i]
        self.e = ev["MET_e"][i]
        self.p4 = vector.obj(
            pt=self.pt,
            eta=self.eta,
            phi=self.phi,
            e=self.e,
        )
