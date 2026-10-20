"""Channel orders required by registered models. Source: research/upstream/CAMER/parameters.py
and the EAV paper (Lee et al., Scientific Data 2024). Kept here so the worker has no runtime
dependency on the upstream repo."""

DEAP_MAHNOB_CHANNEL_ORDER = [
    "FP1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7",
    "CP5", "CP1", "P3", "P7", "PO3", "O1", "OZ", "PZ",
    "FP2", "AF4", "FZ", "F4", "F8", "FC6", "FC2", "CZ",
    "C4", "T8", "CP6", "CP2", "P4", "P8", "PO4", "O2",
]

AMIGOS_CHANNEL_ORDER = [
    "AF3", "F7", "F3", "FC5", "T7", "P7", "O1",
    "O2", "P8", "T8", "FC6", "F4", "F8", "AF4",
]

EAV_CHANNEL_ORDER = [
    "Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "FC5",
    "FC1", "FC2", "FC6", "T7", "C3", "Cz", "C4", "T8",
    "CP5", "CP1", "CP2", "CP6", "P7", "P3", "Pz", "P4",
    "P8", "PO9", "O1", "Oz", "O2", "PO10",
]

CHANNEL_ORDERS = {
    "camer_deap": DEAP_MAHNOB_CHANNEL_ORDER,
    "camer_mahnob": DEAP_MAHNOB_CHANNEL_ORDER,
    "camer_amigos": AMIGOS_CHANNEL_ORDER,
    "camer_eav": EAV_CHANNEL_ORDER,
    "biosemi32_first32": DEAP_MAHNOB_CHANNEL_ORDER,
}
