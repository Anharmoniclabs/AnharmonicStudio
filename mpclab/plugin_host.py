"""VST3 hosting in disposable child processes, with a nonblocking live adapter.

The worker's main thread owns all plugin calls. Only bounded JSON and float32
buffers cross its pipe; the application never imports a third-party plugin DLL.
"""

from __future__ import annotations

import base64
import json
import multiprocessing
import os
from pathlib import Path
import queue
import struct
import threading

import numpy as np

MAX_PACKET = 4 * 1024 * 1024
MAX_FRAMES = 8192
MAX_STATE = 2 * 1024 * 1024


class PluginError(RuntimeError):
    pass


class UnavailablePlugin:
    """Keep a missing saved instrument silent instead of substituting a new sound."""

    def __init__(self, error):
        self.error = error
        self.info = {"parameters": {}}

    def render(self, *args, **kwargs):
        return None

    def close(self):
        pass


def send_packet(connection, header, audio=None):
    metadata = json.dumps(header, allow_nan=False, separators=(",", ":")).encode()
    data = b"" if audio is None else np.asarray(audio, dtype="<f4").tobytes()
    packet = struct.pack("<I", len(metadata)) + metadata + data
    if len(packet) > MAX_PACKET:
        raise PluginError("Plugin message exceeds the size limit")
    connection.send_bytes(packet)


def receive_packet(connection):
    packet = connection.recv_bytes(MAX_PACKET)
    if len(packet) < 4:
        raise PluginError("Incomplete plugin reply")
    size = struct.unpack_from("<I", packet)[0]
    if size > len(packet) - 4:
        raise PluginError("Invalid plugin reply")
    header = json.loads(packet[4 : 4 + size])
    if not isinstance(header, dict):
        raise PluginError("Invalid plugin metadata")
    return header, memoryview(packet)[4 + size :]


def plugin_worker(connection, specification, sample_rate):
    """Entry point for multiprocessing spawn (also supported by frozen builds)."""
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        import pedalboard_native

        plugin_class = pedalboard_native.VST3Plugin
        if Path(specification["path"]).suffix.casefold() == ".component":
            plugin_class = pedalboard_native.AudioUnitPlugin

        def initialize_parameters(self, parameter_values):
            if parameter_values:
                raise PluginError("Unexpected constructor parameters")

        pedalboard_native.ExternalPlugin.__set_initial_parameter_values__ = initialize_parameters
        plugin = plugin_class(
            specification["path"],
            plugin_name=specification.get("plugin_name") or None,
            initialization_timeout=5,
        )
        if specification.get("state"):
            state = base64.b64decode(specification["state"], validate=True)
            if len(state) > MAX_STATE:
                raise PluginError("Plugin state is too large")
            plugin.raw_state = state
        parameters = {
            str(p.index): p
            for p in plugin._parameters
            if p.is_automatable
            and not p.name.startswith("MIDI Ch. ")
            and p.name not in {"Buffer Size", "Sample Rate"}
        }
        for name, value in specification.get("parameters", {}).items():
            if name not in parameters:
                raise PluginError(f"Plugin parameter is missing: {name}")
            parameters[name].raw_value = float(value)
        state = plugin.raw_state
        if len(state) > MAX_STATE:
            raise PluginError("Plugin state exceeds the 2 MiB project limit")
        info = {
            "name": plugin.name,
            "instrument": bool(plugin.is_instrument),
            "effect": bool(plugin.is_effect),
            "latency_samples": int(plugin.reported_latency_samples),
            "state": base64.b64encode(state).decode(),
            "parameters": {
                name: {
                    "name": str(p.name),
                    "value": float(p.raw_value),
                    "display": str(p.string_value),
                }
                for name, p in list(parameters.items())[:512]
            },
        }
        send_packet(connection, info)
        channels = 2
        while True:
            request, raw = receive_packet(connection)
            if request.get("command") == "close":
                break
            frames = request.get("frames", 0)
            if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
                raise PluginError("Invalid plugin audio block")
            if request.get("reset"):
                plugin.reset()
            if plugin.is_instrument:
                midi = [(bytes(event), float(at)) for event, at in request.get("midi", [])]
                try:
                    rendered = plugin.process(
                        midi,
                        duration=frames / sample_rate,
                        sample_rate=sample_rate,
                        num_channels=channels,
                        buffer_size=frames,
                        reset=False,
                    )
                except ValueError as exc:
                    if channels != 2 or "does not support 2-channel" not in str(exc):
                        raise
                    channels = 1
                    rendered = plugin.process(
                        midi,
                        duration=frames / sample_rate,
                        sample_rate=sample_rate,
                        num_channels=channels,
                        buffer_size=frames,
                        reset=False,
                    )
            else:
                if len(raw) != frames * 2 * 4:
                    raise PluginError("Invalid plugin input buffer")
                audio = np.frombuffer(raw, dtype="<f4").reshape(frames, 2)
                incoming = (
                    np.ascontiguousarray(audio.T)
                    if channels == 2
                    else audio.mean(axis=1, keepdims=True).T
                )
                try:
                    rendered = plugin.process(
                        incoming, sample_rate, buffer_size=frames, reset=False
                    )
                except ValueError as exc:
                    if channels != 2 or "does not support 2-channel" not in str(exc):
                        raise
                    channels = 1
                    rendered = plugin.process(
                        audio.mean(axis=1, keepdims=True).T,
                        sample_rate,
                        buffer_size=frames,
                        reset=False,
                    )
            output = np.asarray(rendered, dtype=np.float32)
            if output.ndim == 1:
                output = np.stack((output, output))
            if output.shape == (1, frames):
                output = np.repeat(output, 2, axis=0)
            if output.shape != (2, frames) or not np.isfinite(output).all():
                raise PluginError("Plugin returned invalid or incomplete audio")
            send_packet(connection, {"frames": frames}, output.T)
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            send_packet(connection, {"error": str(exc)[:2000] or type(exc).__name__})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class IsolatedPlugin:
    def __init__(self, specification, sample_rate=48000, *, worker=plugin_worker, timeout=15):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=worker,
            args=(child, specification, sample_rate),
            daemon=True,
            name="Anharmonic isolated plugin",
        )
        self.closed = False
        self.process.start()
        child.close()
        try:
            self.info, _ = self._receive(timeout)
        except Exception:
            self.close()
            raise

    def _receive(self, timeout):
        if not self.connection.poll(timeout):
            raise PluginError("Plugin stopped responding")
        try:
            header, raw = receive_packet(self.connection)
        except (EOFError, OSError, ValueError) as exc:
            raise PluginError("Plugin process closed unexpectedly") from exc
        if header.get("error"):
            raise PluginError(header["error"])
        return header, raw

    def render(self, audio, frames, midi=(), *, reset=False, timeout=2):
        if self.closed:
            raise PluginError("Plugin is closed")
        try:
            send_packet(self.connection, {"frames": frames, "midi": midi, "reset": reset}, audio)
            header, raw = self._receive(timeout)
            if header.get("frames") != frames or len(raw) != frames * 8:
                raise PluginError("Plugin returned the wrong block length")
            result = np.frombuffer(raw, dtype="<f4").reshape(frames, 2)
            if not np.isfinite(result).all():
                raise PluginError("Plugin returned nonfinite audio")
            return result
        except PluginError:
            self.close()
            raise
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.close()
            raise PluginError("Plugin process closed unexpectedly") from exc

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.connection.close()
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)


class LivePlugin:
    """Two-block pipeline. Callback-side work never waits for third-party DSP."""

    def __init__(self, plugin, blocksize):
        self.plugin = plugin
        self.blocksize = blocksize
        self.info = plugin.info
        self.requests: queue.Queue = queue.Queue(maxsize=4)
        self.results: queue.Queue = queue.Queue(maxsize=4)
        self.error = ""
        self.misses = 0
        self._position = 0
        self._pending = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="Plugin audio bridge", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    sequence, audio, frames, midi, reset = self.requests.get(timeout=0.1)
                except queue.Empty:
                    continue
                output = self.plugin.render(audio, frames, midi, reset=reset)
                try:
                    self.results.put_nowait((sequence, output))
                except queue.Full:
                    self.error = "Plugin output queue overflowed; reload the plugin"
                    break
        except Exception as exc:
            self.error = str(exc) or "Plugin processing failed"
        finally:
            self.plugin.close()

    def render(self, audio, frames, midi=(), *, reset=False):
        if self.error:
            return None
        if not 0 < frames <= self.blocksize:
            self.error = "Plugin buffer changed; use a fixed buffer and reload the plugin"
            return None
        sequence = self._position
        self._position += frames
        try:
            self.requests.put_nowait(
                (sequence, None if audio is None else audio.copy(), frames, list(midi), reset)
            )
        except queue.Full:
            self.error = "Plugin could not keep up; reload it or increase the audio buffer"
            return None
        while True:
            try:
                number, result = self.results.get_nowait()
                self._pending[number] = result
            except queue.Empty:
                break
        wanted = sequence - 2 * self.blocksize
        end = wanted + frames
        result = np.zeros((frames, 2), dtype=np.float32)
        covered = 0
        for number, chunk in list(self._pending.items()):
            first, last = max(number, wanted), min(number + len(chunk), end)
            if last > first:
                result[first - wanted : last - wanted] = chunk[first - number : last - number]
                covered += last - first
            if number + len(chunk) <= end:
                del self._pending[number]
        if covered < max(0, end - max(0, wanted)):
            self.misses += 1
            self.error = "Plugin missed its audio deadline; reload it or increase the buffer"
            return None
        return result

    def close(self):
        self._stop.set()
        self._thread.join(timeout=0.2)
        if self._thread.is_alive():
            self.plugin.close()
            self._thread.join(timeout=1)
