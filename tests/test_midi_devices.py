import queue
import time

from mpclab.midi_devices import MidiPort, MidiRouter, MidiService, controller_settings, stable_ports


def wait_for(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("MIDI worker did not reach expected state")


def make_router():
    calls = []
    bank = [0]
    router = MidiRouter(
        lambda *args: calls.append(("note_on", *args)),
        lambda *args: calls.append(("note_off", *args)),
        lambda *args: calls.append(("pad_on", *args)),
        lambda *args: calls.append(("pad_off", *args)),
        lambda: bank[0],
        lambda *args: calls.append(("control", *args)),
    )
    return router, calls, bank


def test_alsa_reconnect_preserves_identity_and_duplicate_ports_remain_distinct():
    old = stable_ports(
        ["MPK mini 24:MPK mini MIDI 1 24:0", "MPK mini 24:MPK mini MIDI 1 24:0"], "alsa"
    )
    new = stable_ports(
        ["MPK mini 36:MPK mini MIDI 1 36:0", "MPK mini 36:MPK mini MIDI 1 36:0"], "alsa"
    )
    assert [p.id for p in old] == [p.id for p in new]
    assert old[0].id != old[1].id


def test_note_off_uses_original_bank_and_velocity_zero_releases():
    router, calls, bank = make_router()
    router.handle("mpc", [0x99, 36, 100])
    bank[0] = 2
    router.handle("mpc", [0x99, 36, 0])
    assert calls == [("pad_on", 0, 100 / 127), ("pad_off", 0)]


def test_sustain_and_duplicate_controller_note_ownership():
    router, calls, _ = make_router()
    router.handle("a", [0x90, 60, 127])
    router.handle("b", [0x90, 60, 90])
    router.handle("a", [0xB0, 64, 127])
    router.handle("a", [0x80, 60, 0])
    router.handle("b", [0x80, 60, 0])
    assert calls == [("note_on", 60, 1)]
    router.handle("a", [0xB0, 64, 0])
    assert calls[-1] == ("note_off", 60)
    assert not router.held


def test_disconnect_and_overflow_release_held_notes():
    router, calls, _ = make_router()
    router.handle("a", [0x90, 64, 100])
    router.handle("b", [0x90, 65, 100])
    router.handle("a", [])
    assert calls[-1] == ("note_off", 64)
    service = MidiService(queue_size=1)
    service._emit("b", [0x90, 67, 100])
    service._emit("b", [0x80, 65, 0])
    for event in service.drain():
        router.handle(*event)
    assert calls[-1] == ("note_off", 65)
    assert not router.held


def test_learn_routes_arbitrary_pad_notes_and_debounces_transport_cc():
    router, calls, _ = make_router()
    router.learn = ("mpc", "pad:7")
    router.handle("mpc", [0x90, 54, 110])
    assert not calls
    router.handle("mpc", [0x90, 54, 110])
    assert calls[-1] == ("pad_on", 7, 110 / 127)
    router.learn = ("mpc", "record")
    router.handle("mpc", [0xB0, 29, 127])
    router.handle("mpc", [0xB0, 29, 127])
    router.handle("mpc", [0xB0, 29, 127])
    assert calls.count(("control", "record", 127)) == 1
    router.handle("mpc", [0xB0, 29, 0])
    router.handle("mpc", [0xB0, 29, 127])
    assert calls.count(("control", "record", 127)) == 2


def test_bad_settings_and_messages_do_not_break_input():
    result = controller_settings(
        {"a": {"mode": "bad", "pad_base": "oops", "pads": {"60": "bad"}, "cc": {"0:999": "master"}}}
    )
    assert result["a"]["pad_base"] == 36
    router, calls, _ = make_router()
    router.settings = result
    for event in ([0x90], [0x90, 60, 900], [0x90, -1, 50], [0xF8], [2, 2, 2]):
        router.handle("a", event)
    assert not calls


def test_unmapped_expression_reaches_instrument_and_mapped_controls_are_consumed():
    router, calls, _ = make_router()
    expression = []
    router.expression = expression.append
    router.settings["keys"] = {"cc": {"0:7": "master"}}
    router.handle("keys", [0xE3, 0, 100])
    router.handle("keys", [0xB0, 1, 90])
    router.handle("keys", [0xB0, 7, 100])
    assert expression == [[0xE0, 0, 100], [0xB0, 1, 90]]
    assert calls == [("control", "master", 100)]


def test_transport_button_is_rearmed_after_disconnect():
    router, calls, _ = make_router()
    router.settings["keys"] = {"cc": {"0:29": "record"}}
    router.handle("keys", [0xB0, 29, 127])
    router.handle("keys", [])
    router.handle("keys", [0xB0, 29, 127])
    assert calls == [("control", "record", 127)] * 2


def test_worker_hotplug_disable_reconnect_and_shutdown():
    names = ["Keyboard 24:0"]
    handles = []

    class Port:
        def __init__(self):
            self.messages = queue.Queue()
            self.opened = False
            self.deleted = False
            handles.append(self)

        def get_ports(self):
            return list(names)

        def ignore_types(self, **_kwargs):
            pass

        def open_port(self, index, name):
            assert names[index].startswith("Keyboard")
            self.opened = True

        def get_message(self):
            try:
                return self.messages.get_nowait(), 0.01
            except queue.Empty:
                return None

        def close_port(self):
            self.opened = False

        def delete(self):
            self.deleted = True

    service = MidiService(factory=Port)
    service.start()
    try:
        wait_for(lambda: service.ports and service.ports[0].connected)
        key = service.ports[0].id
        opened = next(p for p in handles if p.opened)
        opened.messages.put([0x90, 60, 100])
        wait_for(lambda: not service.events.empty())
        assert service.drain()[0][:2] == (key, (0x90, 60, 100))
        # A fast reconnect can occur between two scans: the stable controller
        # identity stays the same, but its native endpoint must be reopened.
        names[0] = "Keyboard 36:0"
        service.rescan()
        wait_for(lambda: opened.deleted and any(p.opened for p in handles))
        assert service.ports[0].id == key
        assert service.drain()[0][1] == ()
        opened = next(p for p in handles if p.opened)
        names.clear()
        service.rescan()
        wait_for(lambda: not service.ports)
        assert opened.deleted
        assert service.drain()[0][1] == ()
        names.append("Keyboard")
        service.enable(key, False)
        wait_for(lambda: service.ports == (MidiPort(key, "Keyboard", 0, False),))
        assert not any(p.opened for p in handles)
        service.enable(key, True)
        wait_for(lambda: service.ports[0].connected)
        assert service.ports[0].id == key
    finally:
        service.close()
    assert not service._thread.is_alive()
    assert all(p.deleted for p in handles)
