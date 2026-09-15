"""Conservative standard-protocol profiles; hardware claims are kept explicit."""

PROFILES = {
    "keys": {"name": "Keyboard / digital piano", "mode": "Keys", "pad_base": 36},
    "pads": {"name": "Generic 4×4 pads", "mode": "Pads", "pad_base": 36},
    "mixed": {"name": "Keyboard + drum channel", "mode": "Keys + drum channel", "pad_base": 36},
    "mpc": {"name": "MPC — learn pad layout", "mode": "Pads", "pad_base": 36},
}


def connection_help(profile, system):
    if profile == "mpc":
        return (
            "MPC: choose the MIDI port exposed by your model and mode. Use MIDI Learn for pad banks and Q-Link controls. "
            "Enable clock on one input only, or send clock to one output. "
            "To record the MPC's sound, select its supported USB audio channels or connect its audio outputs to an interface. "
            + (
                "Vendor controller-mode support on Linux must be verified for your model. "
                if system == "Linux"
                else "Install the model's required vendor driver when applicable. "
            )
            + "This generic mapping does not control proprietary screens or LEDs."
        )
    return "Connect USB MIDI or a DIN-MIDI interface, select a sound, and play. MIDI carries notes; use an audio input to record your piano's own sound."


def input_channel_choices(channels):
    count = max(0, min(64, int(channels)))
    return [(f"Input {i + 1} · mono", (i,)) for i in range(count)] + [
        (f"Inputs {i + 1}–{i + 2} · stereo", (i, i + 1)) for i in range(0, count - 1, 2)
    ]
