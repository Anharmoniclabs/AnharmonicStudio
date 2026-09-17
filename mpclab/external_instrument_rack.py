"""Owned external-instrument instances keyed by stable project instrument identity."""

from __future__ import annotations

from dataclasses import dataclass


def prism_tempo_events(plugin, bpm):
    """Return the Prism MIDI CC protocol for a tempo update."""
    if getattr(plugin, "info", {}).get("name") != "Anharmonic Prism":
        return []
    if type(bpm) not in (int, float) or not 20 <= bpm <= 400:
        raise ValueError("Prism tempo must be between 20 and 400 BPM")
    value = round(float(bpm) * 100)
    return [
        ([0xBF, 99, 125], 0),
        ([0xBF, 98, 80 + (value >> 14)], 0),
        ([0xBF, 6, (value >> 7) & 127], 0),
        ([0xBF, 38, value & 127], 0),
    ]


@dataclass(eq=False)
class HostedNote:
    route: "HostedInstrumentRoute"
    note: int
    channel: int
    event_source: object = None
    trigger_id: object = None
    sequence_id: str | None = None
    live_trigger: bool = True
    dead: bool = False
    onset: object = None

    def note_off(self, _release=0):
        self.route.release_voice(self)


class HostedInstrumentRoute:
    """One plugin instance plus all MIDI/event ownership that belongs to it."""

    def __init__(self, instrument_id: str | None = None, plugin=None):
        self.instrument_id = instrument_id
        self.plugin = plugin
        self.events = []
        self.ends = []
        self.voices: list[HostedNote] = []
        self._gate_owners = {}
        self.reset_requested = False

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
    ):
        channel = (0 if live else 1) if channel is None else channel
        onset = [0x90 | channel, note, max(1, min(127, round(velocity * 127)))]
        voice = HostedNote(
            self,
            note,
            channel,
            event_source=event_source,
            trigger_id=trigger_id,
            sequence_id=sequence_id,
            live_trigger=live,
            onset=onset,
        )
        self.voices.append(voice)
        self.events.append((onset, offset))
        if gate is not None:
            ending = (offset + gate, channel, note)
            self.ends.append(ending)
            self._gate_owners[id(ending)] = voice
        return voice

    def _has_peer(self, voice):
        return any(
            not other.dead
            and other is not voice
            and other.note == voice.note
            and other.channel == voice.channel
            for other in self.voices
        )

    def release_voice(self, voice, offset=0):
        if voice.dead:
            return
        voice.dead = True
        self.events[:] = [event for event in self.events if event[0] is not voice.onset]
        kept = []
        for ending in self.ends:
            if self._gate_owners.get(id(ending)) is voice:
                self._gate_owners.pop(id(ending), None)
            else:
                kept.append(ending)
        self.ends = kept
        if not self._has_peer(voice):
            self.events.append(([0x80 | voice.channel, voice.note, 0], offset))

    def note_off(self, note, *, channel=0, offset=0):
        owned = [
            voice
            for voice in self.voices
            if voice.note == note and voice.channel == channel and not voice.dead
        ]
        if not owned:
            self.events.append(([0x80 | channel, note, 0], offset))
        for voice in owned:
            self.release_voice(voice, offset)

    def release_sequence(self, sequence_id, offset=0):
        for voice in self.voices:
            if not voice.dead and not voice.live_trigger and voice.sequence_id == sequence_id:
                self.release_voice(voice, offset)

    def release_sequenced(self, offset=0):
        for voice in self.voices:
            if not voice.dead and not voice.live_trigger:
                self.release_voice(voice, offset)

    def panic(self):
        self.events = [([0xB0 | channel, 123, 0], 0) for channel in range(16)]
        self.ends.clear()
        self.voices.clear()
        self._gate_owners.clear()
        self.reset_requested = getattr(self.plugin, "info", {}).get("name") != "Anharmonic Prism"

    def render(self, destination, frames, sample_rate, bpm=120, parameters=None):
        instrument = self.plugin
        if instrument is None:
            self.events.clear()
            self.ends.clear()
            self.voices.clear()
            self._gate_owners.clear()
            return
        future = []
        for ending in sorted(self.ends, key=lambda item: item[0]):
            remaining, channel, note = ending
            voice = self._gate_owners.pop(id(ending), None)
            if voice is not None and voice.dead:
                continue
            if remaining < frames:
                if voice is not None:
                    voice.dead = True
                if voice is None or not self._has_peer(voice):
                    self.events.append(([0x80 | channel, note, 0], max(0, remaining)))
            else:
                next_ending = (remaining - frames, channel, note)
                future.append(next_ending)
                if voice is not None:
                    self._gate_owners[id(next_ending)] = voice
        self.ends = future
        midi = prism_tempo_events(instrument, bpm) + [
            (message, offset / sample_rate)
            for message, offset in sorted(self.events, key=lambda item: item[1])
        ]
        self.events.clear()
        options = {"parameters": parameters} if parameters else {}
        output = instrument.render(
            None,
            frames,
            midi,
            reset=self.reset_requested,
            **options,
        )
        self.reset_requested = False
        self.voices[:] = [voice for voice in self.voices if not voice.dead]
        if output is not None:
            destination += output

    def detach(self):
        plugin, self.plugin = self.plugin, None
        self.events.clear()
        self.ends.clear()
        self.voices.clear()
        self._gate_owners.clear()
        self.reset_requested = False
        return plugin

    def close(self):
        plugin = self.detach()
        if plugin is not None:
            plugin.close()


class HostedInstrumentRack:
    """Lifecycle boundary for the legacy slot and stable-ID hosted instruments."""

    LEGACY = None

    def __init__(self):
        self._routes: dict[str | None, HostedInstrumentRoute] = {
            self.LEGACY: HostedInstrumentRoute(self.LEGACY)
        }

    def route(self, instrument_id: str | None = None, *, create=False) -> HostedInstrumentRoute | None:
        route = self._routes.get(instrument_id)
        if route is None and create:
            route = HostedInstrumentRoute(instrument_id)
            self._routes[instrument_id] = route
        return route

    def plugin(self, instrument_id: str | None = None):
        route = self.route(instrument_id)
        return None if route is None else route.plugin

    def set_plugin(self, instrument_id: str | None, plugin):
        route = self.route(instrument_id, create=True)
        old, route.plugin = route.plugin, plugin
        route.events.clear()
        route.ends.clear()
        route.voices.clear()
        route._gate_owners.clear()
        route.reset_requested = False
        return old

    def remove(self, instrument_id: str | None):
        route = self.route(instrument_id)
        if route is None:
            return None
        old = route.detach()
        if instrument_id is not self.LEGACY:
            self._routes.pop(instrument_id, None)
        return old

    def active_ids(self) -> tuple[str, ...]:
        return tuple(
            instrument_id
            for instrument_id, route in self._routes.items()
            if instrument_id is not None and route.plugin is not None
        )

    def all_routes(self):
        return tuple(self._routes.values())

    def note_on(self, note, velocity, *, instrument_id=None, **kwargs):
        route = self.route(instrument_id, create=True)
        return route.note_on(note, velocity, **kwargs)

    def note_off(self, note, *, instrument_id=None, channel=0, offset=0):
        route = self.route(instrument_id)
        if route is not None:
            route.note_off(note, channel=channel, offset=offset)

    def release_sequence(self, sequence_id, offset=0):
        for route in self.all_routes():
            route.release_sequence(sequence_id, offset)

    def release_sequenced(self, offset=0):
        for route in self.all_routes():
            route.release_sequenced(offset)

    def panic(self):
        for route in self.all_routes():
            route.panic()

    def render(self, instrument_id, destination, frames, sample_rate, bpm=120, parameters=None):
        route = self.route(instrument_id)
        if route is not None:
            route.render(destination, frames, sample_rate, bpm, parameters)

    def close(self):
        for route in self.all_routes():
            route.close()
        self._routes = {self.LEGACY: HostedInstrumentRoute(self.LEGACY)}
