"""Non-destructive record-crate views inferred from existing sample metadata."""

import re

CRATES = (("drums", "DRUMS"), ("melodic", "MELODIC"), ("vinyl", "VINYL"), ("vocals", "VOCALS"))
INSTRUMENTS = (
    "Kicks",
    "Snares",
    "Rimshots",
    "Claps",
    "Hi-hats",
    "Cymbals",
    "Toms",
    "Percussion",
    "Drum loops",
    "Bass",
    "Keys & piano",
    "Guitars",
    "Strings",
    "Synths",
    "Melodic loops",
    "Vocals",
    "Vocal chops",
    "Textures & FX",
    "Full tracks",
    "Samples & recordings",
    "Unsorted samples",
)


def natural_key(text):
    """Keep numbered takes and tempo folders in human reading order."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", text)
    )


def sample_location(clip):
    """Expose existing provenance without moving files or changing sample IDs."""
    if clip.kind == "pack":
        return (clip.pack or "Sample pack", clip.category or "Loose files")
    if clip.kind == "stem":
        return ("Separated stems",)
    if clip.kind == "kit":
        return ("Drum kits",)
    if clip.kind.startswith("vocal"):
        return ("Vocal recordings",)
    if clip.kind in {"render", "chop"}:
        return ("Renders & chops",)
    return ("Imported records",)


def sample_group(clip):
    """Return crate/instrument from names, folders and stem tags, never audio I/O.

    A crate is a browser view, not a claim about a recording's provenance.
    Unknown sounds remain accessible in Vinyl / Unsorted samples.
    """
    text = " ".join(str(getattr(clip, key, "") or "") for key in ("name", "category", "stem"))
    # Split camel case as well as pack conventions such as E808_BD-01.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    tokens = set(re.findall(r"[a-z]+", text))
    loop = bool(tokens & {"loop", "loops", "break", "breaks", "breakbeat"})
    stem = getattr(clip, "stem", "")
    if stem in {"bass", "vocals", "other", "piano", "guitar"}:
        return {
            "bass": ("melodic", "Bass"),
            "vocals": ("vocals", "Vocals"),
            "other": ("melodic", "Melodic loops"),
            "piano": ("melodic", "Keys & piano"),
            "guitar": ("melodic", "Guitars"),
        }[stem]
    if getattr(clip, "kind", "").startswith("vocal"):
        return "vocals", "Vocals"
    if getattr(clip, "stem", "") == "drums":
        return "drums", "Drum loops"
    if getattr(clip, "kind", "") == "source" and getattr(clip, "duration", 0) >= 30:
        return "vinyl", "Full tracks"
    if tokens & {"vocal", "vocals", "vox", "voice", "acapella", "acappella", "choir"}:
        return "vocals", "Vocal chops" if tokens & {"chop", "chops", "cut"} else "Vocals"
    if loop and tokens & {"drum", "drums", "break", "breaks", "breakbeat", "beat", "beats"}:
        return "drums", "Drum loops"
    for words, label in (
        ({"kick", "kicks", "bd", "bassdrum"}, "Kicks"),
        ({"snare", "snares", "sd"}, "Snares"),
        ({"rim", "rimshot", "rimshots", "rs"}, "Rimshots"),
        ({"clap", "claps", "handclap", "cp"}, "Claps"),
        ({"hat", "hats", "hihat", "hihats", "hh", "ch", "oh", "chh", "ohh"}, "Hi-hats"),
        ({"cymbal", "cymbals", "crash", "ride", "cym", "cy"}, "Cymbals"),
        ({"tom", "toms", "lt", "mt", "ht"}, "Toms"),
        (
            {
                "perc",
                "percussion",
                "shaker",
                "conga",
                "congas",
                "bongo",
                "bongos",
                "tambourine",
                "drums",
                "claves",
                "cl",
                "cowbell",
                "cb",
                "maracas",
                "ma",
                "hc",
                "mc",
                "lc",
            },
            "Percussion",
        ),
    ):
        if tokens & words or (label == "Kicks" and {"bass", "drum"} <= tokens):
            return "drums", "Drum loops" if loop else label
    if re.search(r"\b808\b", text) and getattr(clip, "kind", "") == "kit":
        return "melodic", "Bass"
    for words, label in (
        ({"bass", "sub", "subbass"}, "Bass"),
        ({"keys", "key", "piano", "organ", "rhodes", "ep", "keyboard"}, "Keys & piano"),
        ({"guitar", "guitars", "gtr"}, "Guitars"),
        ({"strings", "string", "violin", "cello"}, "Strings"),
        ({"synth", "lead", "pad", "pluck", "arp", "chord", "chords", "melody"}, "Synths"),
    ):
        if tokens & words:
            return "melodic", "Melodic loops" if loop else label
    if tokens & {"fx", "riser", "impact", "noise", "texture", "textures", "foley", "sweep"}:
        return "vinyl", "Textures & FX"
    if getattr(clip, "kind", "") == "source":
        return "vinyl", "Samples & recordings"
    return "vinyl", "Unsorted samples"
