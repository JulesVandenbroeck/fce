import os
import subprocess

from paths import get_fce_home

_BASE_URL = "https://homepage.iihe.ac.be/~kskovpen/fce/datasets/"


def run_dataset_download(detector=None, energy_gev=None, force=False):
    """
    Download FCC-ee datasets.
    detector:   "IDEA" | "CLD" | None (all detectors)
    energy_gev: "91" | "160" | "240" | "365" | None (all energies)
    force:      re-download even if files already exist
    """
    target_dir = os.path.join(get_fce_home(), "datasets")
    os.makedirs(target_dir, exist_ok=True)

    inventory_path = os.path.join(target_dir, "files.txt")
    inventory_url  = _BASE_URL + "files.txt"

    yield "Fetching file list...\n"
    try:
        subprocess.run(["wget", "-q", "-O", inventory_path, inventory_url], check=True)
    except Exception as e:
        yield f"Error: could not fetch file list — {e}\n"
        return

    try:
        with open(inventory_path) as f:
            all_files = [ln.strip() for ln in f if ln.strip()]
    except Exception as e:
        yield f"Error reading file list: {e}\n"
        return

    # Filter by detector and energy
    def _matches(rel):
        parts = rel.replace("\\", "/").split("/")
        if len(parts) < 2:
            return True
        det_match = (detector is None) or (parts[0] == detector)
        en_str    = f"{energy_gev}GeV" if energy_gev else None
        en_match  = (energy_gev is None) or (len(parts) >= 2 and parts[1] == en_str)
        return det_match and en_match

    file_list = [f for f in all_files if _matches(f)]
    total = len(file_list)
    if total == 0:
        yield "Nothing to download for the selected filter.\n"
        return

    skipped = 0
    for idx, relative_path in enumerate(file_list):
        local_path = os.path.join(target_dir, relative_path.replace("/", os.sep))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        if not force and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            skipped += 1
            yield f"[{idx+1}/{total}] Already have {relative_path}\n"
            continue

        yield f"[{idx+1}/{total}] Downloading {relative_path}\n"
        file_url = _BASE_URL + relative_path.lstrip("/")
        try:
            subprocess.run(["wget", "-q", "-O", local_path, file_url], check=True)
        except Exception as e:
            yield f"  Warning: {e}\n"

    yield f"Done. ({skipped}/{total} already present)\n"
