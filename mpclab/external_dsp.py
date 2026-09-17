"""External instrument and master-effect routing shared by playback and export."""

from __future__ import annotations

from contextlib import ExitStack

from .external_instrument_rack import (
    HostedInstrumentRack,
    HostedNote as ExternalNote,
    prism_tempo_events,
)


class ExternalDSP:
    """Compatibility facade over independently owned hosted instrument routes."""

    def __init__(self):
        self.instruments = HostedInstrumentRack()
        self.effect = None

    def _legacy(self):
        return self.instruments.route(None, create=True)

    # The historical global slot remains available for old projects/UI. New
    # instrument-owned instances live in ``self.instruments`` by stable ID.
    @property
    def instrument(self):
        return self.instruments.plugin(None)

    @instrument.setter
    def instrument(self, plugin):
        self.instruments.set_plugin(None, plugin)

    @property
    def events(self):
        return self._legacy().events

    @property
    def ends(self):
        return self._legacy().ends

    @property
    def voices(self):
        return self._legacy().voices

    @property
    def _gate_owners(self):
        return self._legacy()._gate_owners

    @property
    def reset_requested(self):
        return self._legacy().reset_requested

    @reset_requested.setter
    def reset_requested(self, value):
        self._legacy().reset_requested = bool(value)

    def instrument_for(self, instrument_id=None):
        return self.instruments.plugin(instrument_id)

    def set_instrument(self, instrument_id, plugin):
        return self.instruments.set_plugin(instrument_id, plugin)

    def remove_instrument(self, instrument_id):
        return self.instruments.remove(instrument_id)

    def active_instrument_ids(self):
        return self.instruments.active_ids()

    def note_on(
        self,
        note,
        velocity,
        offset=0,
        gate=None,
        live=True,
        channel=None,
        event_source=None,
        trigger_id=None,
        sequence_id=None,
        instrument_id=None,
    ):
        return self.instruments.note_on(
            note,
            velocity,
            instrument_id=instrument_id,
            offset=offset,
            gate=gate,
            live=live,
            channel=channel,
            event_source=event_source,
            trigger_id=trigger_id,
            sequence_id=sequence_id,
        )

    def note_off(self, note, *, instrument_id=None, channel=0, offset=0):
        self.instruments.note_off(
            note, instrument_id=instrument_id, channel=channel, offset=offset
        )

    def release_sequence(self, sequence_id, offset=0):
        self.instruments.release_sequence(sequence_id, offset)

    def release_sequenced(self, offset=0):
        self.instruments.release_sequenced(offset)

    def panic(self):
        self.instruments.panic()

    def render_instrument(
        self,
        destination,
        frames,
        sample_rate,
        bpm=120,
        parameters=None,
        *,
        instrument_id=None,
    ):
        self.instruments.render(
            instrument_id, destination, frames, sample_rate, bpm, parameters
        )

    def render_effect(self, block):
        effect = self.effect
        if effect is not None:
            output = effect.render(block, len(block))
            if output is not None:
                block[:] = output

    def close(self):
        self.instruments.close()
        effect, self.effect = self.effect, None
        if effect is not None:
            effect.close()


class OfflinePlugins:
    def __init__(self, specifications, sample_rate, synth_events, bpm=120):
        from .plugin_host import IsolatedPlugin, PluginError

        self.stack = ExitStack()
        self.sample_rate = sample_rate
        self.bpm = bpm
        self.instrument = self.effect = None
        self.events = []
        self.index = 0
        try:
            for slot, specification in specifications.items():
                if specification.get("bypass"):
                    continue
                plugin = IsolatedPlugin(specification, sample_rate)
                self.stack.callback(plugin.close)
                if not plugin.info.get(slot):
                    raise PluginError(f"Plugin is not an {slot}")
                setattr(self, slot, plugin)
            if self.instrument is not None:
                for event in synth_events:
                    at, note, velocity, gate = event[:4]
                    channel = event[4] if len(event) > 4 else 1
                    self.events.append(
                        (at, [0x90 | channel, note, max(1, min(127, round(velocity * 127)))])
                    )
                    self.events.append((at + gate, [0x80 | channel, note, 0]))
                self.events.sort(key=lambda item: (item[0], item[1][0]))
        except Exception:
            self.stack.close()
            raise

    def render_instrument(self, destination, start, frames, parameters=None):
        if self.instrument is None:
            return
        midi = prism_tempo_events(self.instrument, self.bpm)
        while self.index < len(self.events) and self.events[self.index][0] < start + frames:
            at, message = self.events[self.index]
            midi.append((message, max(0, at - start) / self.sample_rate))
            self.index += 1
        options = {"parameters": parameters} if parameters else {}
        output = self.instrument.render(None, frames, midi, **options)
        if output is not None:
            destination += output

    def render_effect(self, block):
        if self.effect is not None:
            output = self.effect.render(block, len(block))
            if output is not None:
                block[:] = output

    def close(self):
        self.stack.close()
