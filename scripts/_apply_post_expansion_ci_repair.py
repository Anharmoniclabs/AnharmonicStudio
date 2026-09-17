from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


# The project/UI contract is intentionally limited to the four production rates,
# but the low-level Engine/DSP layer is also used by deterministic tests and
# offline probes at compact synthetic rates. Keep that internal contract broad.
replace_once(
    "mpclab/fx_unification.py",
    '''    rate = int(sample_rate)\n    if rate not in (44_100, 48_000, 88_200, 96_000):\n        raise ValueError("unsupported project sample rate")\n''',
    '''    rate = int(sample_rate)\n    if not 1 <= rate <= 768_000:\n        raise ValueError("DSP sample rate must be between 1 Hz and 768 kHz")\n''',
    "low-level DSP sample-rate contract",
)

# Unexpected PortAudio block growth already reallocates the engine's emergency
# scratch buffers. Grow sidechain capture buffers at that same boundary instead
# of failing later inside begin_block().
replace_once(
    "mpclab/sidechain.py",
    '''    def track_order(self) -> tuple[int, ...]:\n        return self._track_order\n\n    def begin_block(self, stamp, frames: int) -> None:\n''',
    '''    def track_order(self) -> tuple[int, ...]:\n        return self._track_order\n\n    def ensure_blocksize(self, frames: int) -> None:\n        """Grow capture buffers at the engine's existing emergency resize boundary."""\n        frames = max(1, int(frames))\n        if frames <= self.blocksize:\n            return\n        self.blocksize = frames\n        self._buffers = {\n            route_id: np.zeros((frames, 2), dtype=np.float32)\n            for route_id in self._buffers\n        }\n        self._slot_buffers = {\n            key: np.zeros((frames, 2), dtype=np.float32)\n            for key in self._slot_buffers\n        }\n        self._stamp = None\n\n    def begin_block(self, stamp, frames: int) -> None:\n''',
    "sidechain emergency block growth",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''        if hasattr(engine, "plugin_pdc"):\n            engine.plugin_pdc.ensure_blocksize(frames)\n''',
    '''        if hasattr(engine, "plugin_pdc"):\n            engine.plugin_pdc.ensure_blocksize(frames)\n        if hasattr(engine, "sidechains"):\n            engine.sidechains.ensure_blocksize(frames)\n''',
    "engine sidechain growth boundary",
)

# Shared renderer modules must import without pulling the third-party plugin host
# (and therefore device/runtime dependencies). Load plugin hosting only when an
# offline owned instrument is actually opened.
replace_once(
    "mpclab/offline_owned_instruments.py",
    '''from .expansion_instruments import ExpansionInstrumentBridge\nfrom .plugin_host import IsolatedPlugin, PluginError\nfrom .plugin_latency import (\n''',
    '''from .expansion_instruments import ExpansionInstrumentBridge\nfrom .plugin_latency import (\n''',
    "remove eager plugin host import",
)

replace_once(
    "mpclab/offline_owned_instruments.py",
    '''    def _open_processors(self) -> None:\n        expansion = getattr(self.project, "daw_expansion", {})\n''',
    '''    def _open_processors(self) -> None:\n        from .plugin_host import IsolatedPlugin, PluginError\n\n        expansion = getattr(self.project, "daw_expansion", {})\n''',
    "lazy plugin host open",
)

replace_once(
    "mpclab/offline_owned_instruments.py",
    '''        if block.shape != (frames, 2) or not np.isfinite(block).all():\n            raise PluginError("Owned instrument returned invalid offline audio")\n''',
    '''        if block.shape != (frames, 2) or not np.isfinite(block).all():\n            from .plugin_host import PluginError\n\n            raise PluginError("Owned instrument returned invalid offline audio")\n''',
    "lazy plugin error import",
)

# The schema assertion predates stable hosted-instrument and DAW-expansion state.
replace_once(
    "tests/test_project_schema.py",
    '''        "midi_files",\n        "track_folders",\n    }\n''',
    '''        "midi_files",\n        "track_folders",\n        "instrument_plugins",\n        "daw_expansion",\n    }\n''',
    "authoritative extension field assertion",
)
