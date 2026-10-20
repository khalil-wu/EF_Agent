"""Channel-name helpers shared by readers, compat checks and the MNE bridge. Pure Python (no MNE import).

clean_name strips recording decorations so "EEG Fp1-REF", "Fp1-A1", "FP1.", "RAW_TP9" all compare as a 10-20 name.
guess_channel_type classifies by name only; files that carry channel types (FIF, BDF status…) take precedence.
"""

from __future__ import annotations

import re

# Channel types whose samples are voltages (MNE stores them in V, the project in µV).
VOLT_TYPES = {"eeg", "eog", "ecg", "emg", "seeg", "ecog", "dbs"}
MNE_TYPES = VOLT_TYPES | {"stim", "misc", "resp", "bio", "syst", "temperature", "gsr"}

TEN_TWENTY = {
    "fp1", "fp2", "fpz", "af3", "af4", "af7", "af8", "afz", "f7", "f3", "fz", "f4", "f8", "f5", "f6", "f1", "f2",
    "fc5", "fc1", "fc2", "fc6", "fc3", "fc4", "fcz", "ft7", "ft8", "ft9", "ft10", "t7", "c3", "cz", "c4", "t8", "c5", "c6",
    "c1", "c2", "cp5", "cp1", "cp2", "cp6", "cp3", "cp4", "cpz", "tp7", "tp8", "tp9", "tp10", "p7", "p3", "pz", "p4", "p8",
    "p5", "p6", "p1", "p2", "p9", "p10", "po3", "po4", "po7", "po8", "poz", "po9", "po10", "o1", "oz", "o2", "iz",
    "t3", "t4", "t5", "t6", "a1", "a2", "m1", "m2",
}
# Old 10-20 names → 10-10 names (ACNS Guideline 3); used when a montage only knows one of them.
OLD_TO_NEW = {"t3": "t7", "t4": "t8", "t5": "p7", "t6": "p8"}

_PREFIX = re.compile(r"^(eeg|raw)[\s_:\-]+", re.I)
_SUFFIX = re.compile(r"[\s_\-/]+(ref|le|re|avg|ave|average|ar|car|a1|a2|a12|m1|m2|m12|cms|linked|mastoids?)$", re.I)

_STIM = re.compile(r"(^sti(\s?\d+)?$|status|trigger|^trig|stim|marker|^events?$|annotation)", re.I)
_EOG = re.compile(r"(eog|^[hv]eo[gl]?|^eye)", re.I)
_ECG = re.compile(r"(ecg|ekg)", re.I)
_EMG = re.compile(r"emg", re.I)
_MISC = re.compile(r"(gsr|^eda|resp|temp|plet|ppg|audio|photo|accel|^acc[_\s]|^acc\d|gyro|^exg\d+$|^erg|pulse|spo2|"
                   r"battery|counter|sample\s*index|timestamp|^time$|^other|^aux|^analog|^x_|^y_|^z_|^delta_|^theta_|^alpha_|"
                   r"^beta_|^gamma_|hsi|headband|elements|^mag|^bip)", re.I)


def clean_name(name: str) -> str:
    """'EEG Fp1-REF' → 'Fp1'. Keeps bipolar pairs like 'Fp1-F7' intact."""
    n = str(name).strip().rstrip(".")
    n = _PREFIX.sub("", n)
    n = _SUFFIX.sub("", n)
    return n.strip()


def normalize_channel(name: str) -> str:
    """Canonical electrode key, including the equivalent old 10-20 names."""
    key = clean_name(name).lower()
    return OLD_TO_NEW.get(key, key)


def is_ten_twenty(name: str) -> bool:
    return normalize_channel(name) in TEN_TWENTY


def guess_channel_type(name: str) -> str:
    n = str(name).strip()
    if is_ten_twenty(n):
        return "eeg"
    if _STIM.search(n):
        return "stim"
    if _EOG.search(n):
        return "eog"
    if _ECG.search(n):
        return "ecg"
    if _EMG.search(n):
        return "emg"
    if _MISC.search(n):
        return "misc"
    return "eeg"


def guess_channel_types(names: list[str]) -> list[str]:
    return [guess_channel_type(n) for n in names]


def merge_channel_types(names: list[str], file_types: list[str] | None) -> list[str]:
    """File-declared types win, except the generic 'eeg' that many formats (EDF, EEGLAB) give every channel."""
    guessed = guess_channel_types(names)
    if not file_types or len(file_types) != len(names):
        return guessed
    out = []
    for g, f in zip(guessed, file_types):
        f = str(f)
        out.append(g if f == "eeg" else (f if f in MNE_TYPES else "misc"))
    return out


def eeg_indices(types: list[str]) -> list[int]:
    return [i for i, t in enumerate(types) if t == "eeg"]
