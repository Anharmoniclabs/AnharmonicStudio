from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/external_dsp.py",
    '''    def active_instrument_ids(self):
        return self.instruments.active_ids()

    def note_on(
''',
    '''    def active_instrument_ids(self):
        return self.instruments.active_ids()

    def plugin_map(self):
        plugins = {}
        legacy = self.instrument_for(None)
        if legacy is not None:
            plugins[None] = legacy
        for instrument_id in self.active_instrument_ids():
            plugin = self.instrument_for(instrument_id)
            if plugin is not None:
                plugins[instrument_id] = plugin
        return plugins

    def queue_event(self, message, offset=0, *, instrument_id=None):
        route = self.instruments.route(instrument_id)
        if route is None or route.plugin is None:
            return False
        route.events.append((message, max(0, int(offset))))
        return True

    def all_voices(self):
        return tuple(voice for route in self.instruments.all_routes() for voice in route.voices)

    def note_on(
''',
    "external routing helpers",
)

replace_once(
    "mpclab/engine.py",
    '''        if (
            instrument_id is None
            and voices is self.synth_voices
            and self.external.instrument is not None
        ):
            channel = midi_channel
            if channel == 0 and not live_trigger:
                channel = 1
            self.external.note_on(
                note,
                velocity,
                offset,
                gate_frames,
                live_trigger,
                channel=channel,
                sequence_id=sequence_id,
                event_source=event_source,
                trigger_id=trigger_id,
            )
            self._trace(
                "synth_on",
                source=note,
                owner=event_source if event_source is not None else sequence_id,
                reason="external_host",
                offset=offset,
            )
            return
''',
    '''        hosted = self.external.instrument_for(instrument_id)
        if voices is self.synth_voices and hosted is not None:
            channel = midi_channel
            if channel == 0 and not live_trigger:
                channel = 1
            hosted_voice = self.external.note_on(
                note,
                velocity,
                offset,
                gate_frames,
                live_trigger,
                channel=channel,
                sequence_id=sequence_id,
                event_source=event_source,
                trigger_id=trigger_id,
                instrument_id=instrument_id,
            )
            owner = instrument_id
            if owner is None:
                owner = event_source if event_source is not None else sequence_id
            self._trace(
                "synth_on",
                source=note,
                voice=hosted_voice,
                owner=owner,
                reason="external_host",
                offset=offset,
            )
            return
''',
    "hosted spawn",
)

replace_once(
    "mpclab/engine.py",
    '''    def _release_synth(
        self, note: int, instrument_id=None, midi_owner=None, midi_channel=0
    ) -> None:
        if instrument_id is None and self.external.instrument is not None:
            self.external.note_off(note)
        for voice in self.synth_voices:
''',
    '''    def _release_synth(
        self, note: int, instrument_id=None, midi_owner=None, midi_channel=0
    ) -> None:
        if self.external.instrument_for(instrument_id) is not None:
            self.external.note_off(
                note,
                instrument_id=instrument_id,
                channel=midi_channel,
            )
            self._trace(
                "synth_release",
                source=note,
                owner=instrument_id,
                reason="external_host_note_off",
            )
        for voice in self.synth_voices:
''',
    "hosted release",
)

replace_once(
    "mpclab/midi_performance.py",
    '''        elif e.external.instrument is not None and instrument is None:
            e.external.events.append(
                ([0x90 | channel, note, max(1, round(velocity * 127))], self.offset)
            )
        else:
''',
    '''        elif e.external.instrument_for(instrument) is not None:
            e.external.note_on(
                note,
                velocity,
                self.offset,
                live=True,
                channel=channel,
                midi_owner=port if False else None,
                instrument_id=instrument,
            )
        else:
'''.replace('                midi_owner=port if False else None,\n', ''),
    "midi hosted note on",
)

replace_once(
    "mpclab/midi_performance.py",
    '''        elif e.external.instrument is not None and instrument is None:
            e.external.events.append(
                ([0x80 | channel, note, self.router.release_velocity], self.offset)
            )
        else:
''',
    '''        elif e.external.instrument_for(instrument) is not None:
            e.external.note_off(
                note,
                instrument_id=instrument,
                channel=channel,
                offset=self.offset,
            )
        else:
''',
    "midi hosted note off",
)

replace_once(
    "mpclab/midi_performance.py",
    '''    def _expression(self, message):
        self.engine.external.events.append((message, self.offset))
''',
    '''    def _expression(self, message):
        instrument_id = self.route[1]
        if not self.engine.external.queue_event(
            message, self.offset, instrument_id=instrument_id
        ):
            self.engine.external.queue_event(message, self.offset, instrument_id=None)
''',
    "midi hosted expression",
)

replace_once(
    "mpclab/event_source.py",
    '''    for collection in (engine.voices, engine.synth_voices, engine.external.voices):
''',
    '''    for collection in (engine.voices, engine.synth_voices, engine.external.all_voices()):
''',
    "all hosted event voices",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''    for frame, control in midi_controls:
        if control.instrument is None and control.pad is None:
            engine.external.events.append((control.message, max(0, frame)))
''',
    '''    for frame, control in midi_controls:
        if control.pad is None:
            engine.external.queue_event(
                control.message,
                max(0, frame),
                instrument_id=control.instrument,
            )
''',
    "sequenced hosted controls",
)

mix_path = Path("mpclab/engine_mixing.py")
mix = mix_path.read_text()
start_marker = "    # Deliver Prism curves with their audio block, without UI timers or camera work.\n"
end_marker = "\n    # 4 ─ inserts → track buses → arbitrary routing → sends → master\n"
start = mix.index(start_marker)
end = mix.index(end_marker, start)
replacement = '''    # Render every hosted instrument as its own owned path. Native/built-in
    # tracks are delayed to the slowest hosted route; each faster hosted route
    # receives only its differential PDC before entering its mixer track.
    hosted = []
    legacy = engine.external.instrument_for(None)
    if legacy is not None:
        hosted.append((None, legacy, proj.validate_track_index(proj.synth.track, "synth output")))
    for instrument in proj.instruments:
        plugin = engine.external.instrument_for(instrument.id)
        if plugin is not None:
            hosted.append(
                (
                    instrument.id,
                    plugin,
                    proj.validate_track_index(instrument.patch.track, "instrument output"),
                )
            )

    external_bus = getattr(engine, "_external_instrument", None)
    dry_pdc = getattr(engine, "plugin_pdc", None)
    instrument_pdc = getattr(engine, "instrument_pdc", None)
    if hosted and dry_pdc is not None and dry_pdc.delay_samples > 0:
        dry_pdc.process(tbuf, frames)

    for instrument_id, plugin, target_track in hosted:
        prism_parameters = {}
        if (
            engine.playing
            and engine.mode == "song"
            and getattr(plugin, "info", {}).get("name") == "Anharmonic Prism"
        ):
            prism_parameters = automation_parameters(
                proj, start_beat, getattr(engine, "prism_gesture_targets", ())
            )
        if external_bus is None:
            engine.external.render_instrument(
                tbuf[target_track],
                frames,
                engine.sr,
                proj.bpm,
                prism_parameters,
                instrument_id=instrument_id,
            )
            continue
        external = external_bus[:frames]
        external.fill(0.0)
        engine.external.render_instrument(
            external,
            frames,
            engine.sr,
            proj.bpm,
            prism_parameters,
            instrument_id=instrument_id,
        )
        if instrument_pdc is not None:
            instrument_pdc.process(instrument_id, external, frames)
        np.add(tbuf[target_track], external, out=tbuf[target_track])
'''
mix_path.write_text(mix[:start] + replacement + mix[end:])

replace_once(
    "mpclab/workflow_routing.py",
    '''    from .plugin_latency import PluginDelayCompensator, plugin_path_latency_samples
''',
    '''    from .plugin_latency import HostedInstrumentDelayBank, PluginDelayCompensator
''',
    "pdc imports",
)

replace_once(
    "mpclab/workflow_routing.py",
    '''        if not hasattr(engine, "plugin_pdc") or engine.plugin_pdc.tracks != count:
            engine.plugin_pdc = PluginDelayCompensator(count, engine.blocksize)
        else:
            engine.plugin_pdc.configure(0, engine.blocksize)
        engine._routing_plan = compile_routing(engine.project)
        engine.linux_audio.lock_arrays(
            engine._routing_buses,
            engine._external_instrument,
            engine.plugin_pdc.history,
            engine.plugin_pdc.output,
        )
''',
    '''        if not hasattr(engine, "plugin_pdc") or engine.plugin_pdc.tracks != count:
            engine.plugin_pdc = PluginDelayCompensator(count, engine.blocksize)
        else:
            engine.plugin_pdc.configure(0, engine.blocksize)
        engine.instrument_pdc = HostedInstrumentDelayBank(engine.blocksize)
        engine._routing_plan = compile_routing(engine.project)
        engine.linux_audio.lock_arrays(
            engine._routing_buses,
            engine._external_instrument,
            engine.plugin_pdc.history,
            engine.plugin_pdc.output,
        )
''',
    "allocate hosted pdc",
)

replace_once(
    "mpclab/workflow_routing.py",
    '''    def prepare_plugin_latency(engine):
        delay = plugin_path_latency_samples(engine.external.instrument, include_live_bridge=True)
        engine.plugin_pdc.configure(delay, engine.blocksize)
        engine.linux_audio.lock_arrays(engine.plugin_pdc.history, engine.plugin_pdc.output)
        return delay
''',
    '''    def prepare_plugin_latency(engine):
        delay = engine.instrument_pdc.configure(
            engine.external.plugin_map(), engine.blocksize
        )
        engine.plugin_pdc.configure(delay, engine.blocksize)
        engine.linux_audio.lock_arrays(
            engine.plugin_pdc.history,
            engine.plugin_pdc.output,
            *engine.instrument_pdc.locked_arrays(),
        )
        return delay
''',
    "prepare multi instrument latency",
)

# Ensure project/routing refresh also recomputes hosted instrument latency.
replace_once(
    "mpclab/workflow_routing.py",
    '''        refresh = getattr(engine, "prepare_plugin_chain_latency", None)
        if refresh is not None:
            refresh()
        return plan
''',
    '''        prepare_plugin_latency(engine)
        refresh = getattr(engine, "prepare_plugin_chain_latency", None)
        if refresh is not None:
            refresh()
        return plan
''',
    "routing pdc refresh",
)

Path("tests/test_hosted_instrument_runtime.py").write_text(
    '''from types import SimpleNamespace\n\nimport numpy as np\n\nfrom mpclab.engine import Engine\nfrom mpclab.external_dsp import ExternalDSP\nfrom mpclab.model import SynthPatch\nfrom mpclab.plugin_latency import HostedInstrumentDelayBank\n\n\nclass Plugin:\n    def __init__(self, value=0.0, latency=0, blocksize=0):\n        self.value = value\n        self.blocksize = blocksize\n        self.info = {\n            "name": "Test",\n            "instrument": True,\n            "latency_samples": latency,\n        }\n        self.blocks = []\n        self.closed = False\n\n    def render(self, audio, frames, midi=(), **kwargs):\n        self.blocks.append((list(midi), kwargs))\n        return np.full((frames, 2), self.value, np.float32)\n\n    def close(self):\n        self.closed = True\n\n\nclass Library:\n    pass\n\n\ndef test_owned_routes_keep_midi_and_lifecycle_isolated():\n    external = ExternalDSP()\n    first, second = Plugin(0.1), Plugin(0.2)\n    external.set_instrument("first", first)\n    external.set_instrument("second", second)\n    external.note_on(60, 1, instrument_id="first", channel=2)\n    external.note_on(67, 0.5, instrument_id="second", channel=3)\n\n    one = np.zeros((8, 2), np.float32)\n    two = np.zeros((8, 2), np.float32)\n    external.render_instrument(one, 8, 1000, instrument_id="first")\n    external.render_instrument(two, 8, 1000, instrument_id="second")\n\n    assert first.blocks[0][0] == [([0x92, 60, 127], 0.0)]\n    assert second.blocks[0][0] == [([0x93, 67, 64], 0.0)]\n    np.testing.assert_array_equal(one, np.float32(0.1))\n    np.testing.assert_array_equal(two, np.float32(0.2))\n\n    removed = external.remove_instrument("first")\n    removed.close()\n    assert first.closed\n    assert not second.closed\n    assert external.instrument_for("second") is second\n\n\ndef test_engine_routes_stable_instrument_to_its_owned_host():\n    engine = Engine(Library(), sample_rate=48000, blocksize=64)\n    owned = engine.project.add_instrument("Hosted", SynthPatch(track=4))\n    plugin = Plugin()\n    engine.external.set_instrument(owned.id, plugin)\n\n    engine._spawn_synth(61, 0.75, instrument_id=owned.id, midi_channel=5)\n    assert not engine.synth_voices\n    route = engine.external.instruments.route(owned.id)\n    assert len(route.voices) == 1\n    assert route.voices[0].channel == 5\n    assert route.voices[0].note == 61\n\n    engine._release_synth(61, instrument_id=owned.id, midi_channel=5)\n    assert route.voices[0].dead\n\n\ndef test_hosted_delay_bank_aligns_faster_paths_to_slowest():\n    fast = SimpleNamespace(info={"latency_samples": 4}, blocksize=0)\n    slow = SimpleNamespace(info={"latency_samples": 7}, blocksize=0)\n    bank = HostedInstrumentDelayBank(4)\n    assert bank.configure({"fast": fast, "slow": slow}) == 7\n    assert bank.path_latencies == {"fast": 4, "slow": 7}\n    assert bank.delays["fast"].delay_samples == 3\n    assert bank.delays["slow"].delay_samples == 0\n\n    fast_block = np.zeros((4, 2), np.float32)\n    fast_block[0] = 1\n    bank.process("fast", fast_block, 4)\n    np.testing.assert_array_equal(fast_block[:, 0], [0, 0, 0, 1])\n\n    slow_block = np.zeros((4, 2), np.float32)\n    slow_block[0] = 1\n    bank.process("slow", slow_block, 4)\n    np.testing.assert_array_equal(slow_block[:, 0], [1, 0, 0, 0])\n\n\ndef test_plugin_map_includes_legacy_and_owned_routes():\n    external = ExternalDSP()\n    legacy, owned = Plugin(), Plugin()\n    external.instrument = legacy\n    external.set_instrument("stable", owned)\n    assert external.plugin_map() == {None: legacy, "stable": owned}\n'''
)
