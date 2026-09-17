from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/engine.py",
    '''        if pad.retrigger == "ignore":
            for other in self.voices:
                if not other.dead and self._same_pad_retrigger(other, v, index):
                    self._trace(
                        "pad_ignore",
                        source=index,
                        voice=other,
                        owner=self._voice_owner(other),
                        reason="ignore_retrigger",
                        offset=v.start_offset,
                    )
                    return
''',
    '''        if pad.retrigger == "ignore":
            for other in self.voices:
                if other.dead or not self._same_pad_retrigger(other, v, index):
                    continue
                stronger = self._pad_cut_reason(other, v, pad, index)
                if stronger in ("choke_group", "self_choke"):
                    continue
                self._trace(
                    "pad_ignore",
                    source=index,
                    voice=other,
                    owner=self._voice_owner(other),
                    reason="ignore_retrigger",
                    offset=v.start_offset,
                )
                return
''',
    "ignore precedence",
)

replace_once(
    "mpclab/ui/padgrid.py",
    '''        mode = QComboBox()
        mode.addItems(MODES)
        mode.setCurrentText(pad.mode)
        mode.currentTextChanged.connect(lambda t: (setattr(pad, "mode", t), self.changed.emit()))
        form.addRow(self._lbl("MODE"), mode)
''',
    '''        mode = QComboBox()
        mode.addItems(MODES)
        mode.setCurrentText(pad.mode)

        def set_mode(value):
            old_mode = pad.mode
            old_default = "layer" if old_mode == "one-shot" else "restart"
            if pad.retrigger == old_default:
                pad.retrigger = "layer" if value == "one-shot" else "restart"
            pad.mode = value
            self.changed.emit()

        mode.currentTextChanged.connect(set_mode)
        form.addRow(self._lbl("MODE"), mode)
''',
    "mode compatibility",
)

path = Path("tests/test_pad_retrigger_policy.py")
text = path.read_text()
text += '''\n\ndef test_ignore_does_not_override_explicit_choke_group():\n    engine = engine_with_policy("ignore")\n    pad = engine.project.pads[0]\n    pad.choke = 2\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    engine._spawn(pad, 0, 1)\n    assert len(engine.voices) == 2\n    assert first.length == max(1, int(FADE * SR))\n\n\ndef test_ignore_does_not_override_global_self_choke():\n    engine = engine_with_policy("ignore")\n    engine.project.self_choke = True\n    pad = engine.project.pads[0]\n    engine._spawn(pad, 0, 1)\n    first = engine.voices[0]\n    engine._spawn(pad, 0, 1)\n    assert len(engine.voices) == 2\n    assert first.length == max(1, int(FADE * SR))\n'''
path.write_text(text)
