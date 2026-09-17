from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/model.py",
    'MODES = ("one-shot", "gate", "loop")\nPROJECT_FORMAT_VERSION = 6\n',
    'MODES = ("one-shot", "gate", "loop")\n'
    'RETRIGGER_POLICIES = ("restart", "layer", "ignore", "crossfade")\n'
    'PROJECT_FORMAT_VERSION = 6\n',
    "retrigger constants",
)

replace_once(
    "mpclab/model.py",
    '    mode: str = "one-shot"\n'
    '    loop_crossfade: float = 0.005  # seconds shared between the loop tail/head\n',
    '    mode: str = "one-shot"\n'
    '    retrigger: str | None = None  # restart | layer | ignore | crossfade\n'
    '    loop_crossfade: float = 0.005  # seconds shared between the loop tail/head\n',
    "pad retrigger field",
)

replace_once(
    "mpclab/model.py",
    '    def __post_init__(self):\n'
    '        if type(self.root_note) is not int or not 0 <= self.root_note <= 127:\n'
    '            raise ValueError("sample root_note must be an integer from 0 to 127")\n'
    '        if type(self.mono) is not bool:\n'
    '            raise ValueError("sample mono must be a boolean")\n',
    '    def __post_init__(self):\n'
    '        if self.retrigger is None:\n'
    '            self.retrigger = "layer" if self.mode == "one-shot" else "restart"\n'
    '        if self.retrigger not in RETRIGGER_POLICIES:\n'
    '            raise ValueError("pad retrigger policy is invalid")\n'
    '        if type(self.root_note) is not int or not 0 <= self.root_note <= 127:\n'
    '            raise ValueError("sample root_note must be an integer from 0 to 127")\n'
    '        if type(self.mono) is not bool:\n'
    '            raise ValueError("sample mono must be a boolean")\n',
    "pad post init",
)

replace_once(
    "mpclab/model.py",
    '            if pad.mode not in MODES:\n'
    '                raise ValueError("pad mode must be one-shot, gate, or loop")\n'
    '            for field_name in ("reverse", "mono"):\n',
    '            if pad.mode not in MODES:\n'
    '                raise ValueError("pad mode must be one-shot, gate, or loop")\n'
    '            if pad.retrigger not in RETRIGGER_POLICIES:\n'
    '                raise ValueError("pad retrigger policy is invalid")\n'
    '            for field_name in ("reverse", "mono"):\n',
    "pad validation",
)

replace_once(
    "mpclab/engine.py",
    '        fade = int(FADE * self.sr)\n'
    '        for other in self.voices:\n',
    '        fade = int(FADE * self.sr)\n'
    '        if pad.retrigger == "ignore":\n'
    '            for other in self.voices:\n'
    '                if not other.dead and self._same_pad_retrigger(other, v, index):\n'
    '                    self._trace(\n'
    '                        "pad_ignore",\n'
    '                        source=index,\n'
    '                        voice=other,\n'
    '                        owner=self._voice_owner(other),\n'
    '                        reason="ignore_retrigger",\n'
    '                        offset=v.start_offset,\n'
    '                    )\n'
    '                    return\n'
    '        if pad.retrigger == "crossfade":\n'
    '            v.attack = max(v.attack, fade)\n'
    '        for other in self.voices:\n',
    "spawn retrigger preflight",
)

replace_once(
    "mpclab/engine.py",
    '    def _pad_cut_reason(\n'
    '        self, previous: PadVoice, current: PadVoice, pad: Pad, index: int\n'
    '    ) -> str | None:\n',
    '    @staticmethod\n'
    '    def _same_pad_retrigger(previous: PadVoice, current: PadVoice, index: int) -> bool:\n'
    '        if previous.pad_index != index or previous.note is not None or current.note is not None:\n'
    '            return False\n'
    '        if previous.live_trigger != current.live_trigger:\n'
    '            return False\n'
    '        return current.live_trigger or previous.sequence_id == current.sequence_id\n'
    '\n'
    '    def _pad_cut_reason(\n'
    '        self, previous: PadVoice, current: PadVoice, pad: Pad, index: int\n'
    '    ) -> str | None:\n',
    "same-pad retrigger helper",
)

replace_once(
    "mpclab/engine.py",
    '        if pad.choke and previous.choke == pad.choke:\n'
    '            return "choke_group"\n'
    '        if pad.mode != "one-shot" and previous.pad_index == index:\n'
    '            return "pad_retrigger"\n'
    '        if (\n'
    '            self.project.self_choke\n'
    '            and current.source_id\n'
    '            and previous.source_id == current.source_id\n'
    '        ):\n'
    '            return "self_choke"\n'
    '        return None\n',
    '        if pad.choke and previous.choke == pad.choke:\n'
    '            return "choke_group"\n'
    '        if (\n'
    '            self.project.self_choke\n'
    '            and current.source_id\n'
    '            and previous.source_id == current.source_id\n'
    '        ):\n'
    '            return "self_choke"\n'
    '        if self._same_pad_retrigger(previous, current, index):\n'
    '            if pad.retrigger == "restart":\n'
    '                return "pad_restart"\n'
    '            if pad.retrigger == "crossfade":\n'
    '                return "pad_crossfade"\n'
    '        return None\n',
    "pad cut policy",
)

replace_once(
    "mpclab/audio_trace.py",
    '    "pad_on",\n    "pad_release",\n    "pad_steal",\n',
    '    "pad_on",\n    "pad_release",\n    "pad_steal",\n    "pad_ignore",\n',
    "pad ignore trace kind",
)

replace_once(
    "mpclab/ui/padgrid.py",
    'from ..model import PADS_PER_BANK, DISPLAY_ORDER, PAD_KEYS, MODES\n',
    'from ..model import PADS_PER_BANK, DISPLAY_ORDER, PAD_KEYS, MODES, RETRIGGER_POLICIES\n',
    "padgrid retrigger import",
)

replace_once(
    "mpclab/ui/padgrid.py",
    '        mode = QComboBox()\n'
    '        mode.addItems(MODES)\n'
    '        mode.setCurrentText(pad.mode)\n'
    '        mode.currentTextChanged.connect(lambda t: (setattr(pad, "mode", t), self.changed.emit()))\n'
    '        form.addRow(self._lbl("MODE"), mode)\n'
    '\n'
    '        choke = QComboBox()\n',
    '        mode = QComboBox()\n'
    '        mode.addItems(MODES)\n'
    '        mode.setCurrentText(pad.mode)\n'
    '        mode.currentTextChanged.connect(lambda t: (setattr(pad, "mode", t), self.changed.emit()))\n'
    '        form.addRow(self._lbl("MODE"), mode)\n'
    '\n'
    '        retrigger = QComboBox()\n'
    '        for policy in RETRIGGER_POLICIES:\n'
    '            retrigger.addItem(policy.upper(), policy)\n'
    '        retrigger.setCurrentIndex(retrigger.findData(pad.retrigger))\n'
    '        retrigger.setToolTip(\n'
    '            "What happens when this pad is triggered again while its previous voice is active. "\n'
    '            "Choke groups and CUT SOURCE remain independent and can still stop a layered voice."\n'
    '        )\n'
    '        retrigger.currentIndexChanged.connect(\n'
    '            lambda i: (setattr(pad, "retrigger", retrigger.itemData(i)), self.changed.emit())\n'
    '        )\n'
    '        form.addRow(self._lbl("RETRIGGER"), retrigger)\n'
    '\n'
    '        choke = QComboBox()\n',
    "padgrid retrigger control",
)

Path("tests/test_pad_retrigger_policy.py").write_text(
    '''import numpy as np\n\nfrom mpclab.engine import Engine, FADE\nfrom mpclab.model import Pad, Project\n\nSR = 8000\n\n\nclass MemoryLibrary:\n    def __init__(self):\n        self.data = np.full((SR, 2), 0.1, dtype=np.float32)\n\n    def audio(self, ref):\n        return self.data\n\n\ndef engine_with_policy(policy):\n    engine = Engine(MemoryLibrary(), sample_rate=SR, blocksize=128)\n    engine.project.self_choke = False\n    engine.project.pads[0] = Pad(\n        sample_id="sample",\n        start=0,\n        end=1,\n        mode="one-shot",\n        retrigger=policy,\n    )\n    return engine\n\n\ndef test_retrigger_restart_releases_previous_voice():\n    engine = engine_with_policy("restart")\n    pad = engine.project.pads[0]\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    engine._spawn(pad, 0, 1)\n    assert len(engine.voices) == 2\n    assert first.length == max(1, int(FADE * SR))\n\n\ndef test_retrigger_layer_preserves_previous_voice():\n    engine = engine_with_policy("layer")\n    pad = engine.project.pads[0]\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    natural = first.length\n    engine._spawn(pad, 0, 1)\n    assert len(engine.voices) == 2\n    assert first.length == natural\n\n\ndef test_retrigger_ignore_drops_new_hit_and_traces_reason():\n    engine = engine_with_policy("ignore")\n    engine.set_audio_trace_enabled(True, clear=True)\n    pad = engine.project.pads[0]\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    engine._spawn(pad, 0, 1)\n    assert engine.voices == [first]\n    ignored = [event for event in engine.audio_trace_snapshot() if event.kind == "pad_ignore"]\n    assert len(ignored) == 1\n    assert ignored[0].voice == id(first)\n    assert ignored[0].reason == "ignore_retrigger"\n\n\ndef test_retrigger_crossfade_releases_old_and_fades_in_new_voice():\n    engine = engine_with_policy("crossfade")\n    pad = engine.project.pads[0]\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    engine._spawn(pad, 0, 1)\n    second = engine.voices[-1]\n    fade = max(1, int(FADE * SR))\n    assert first.length == fade\n    assert second.attack >= fade\n\n\ndef test_legacy_pad_retrigger_defaults_preserve_old_mode_semantics():\n    project = Project()\n    document = project.to_dict()\n    document["pads"][0].pop("retrigger", None)\n    document["pads"][0]["mode"] = "one-shot"\n    document["pads"][1].pop("retrigger", None)\n    document["pads"][1]["mode"] = "gate"\n    loaded = Project.from_dict(document)\n    assert loaded.pads[0].retrigger == "layer"\n    assert loaded.pads[1].retrigger == "restart"\n\n\ndef test_retrigger_policy_roundtrips_in_project():\n    project = Project()\n    project.pads[0].retrigger = "crossfade"\n    loaded = Project.from_dict(project.to_dict())\n    assert loaded.pads[0].retrigger == "crossfade"\n'''
)
