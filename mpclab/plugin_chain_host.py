"""Isolated VST3/Audio Unit effect chains with auxiliary-audio support.

Every chain lives in one disposable child process. Third-party code never runs
in the audio callback. When a plugin exposes a four-channel input layout through
the host backend, channels 3/4 carry a real stereo sidechain signal rather than
previous-block RMS/control data.
"""

from __future__ import annotations

import base64
import multiprocessing
import os
from pathlib import Path

import numpy as np

from .plugin_host import MAX_FRAMES, MAX_STATE, LivePlugin, PluginError, receive_packet, send_packet

MAX_CHAIN_PLUGINS = 8


def _plugin_parameters(plugin) -> dict[str, object]:
    return {
        str(parameter.index): parameter
        for parameter in plugin._parameters
        if parameter.is_automatable
        and not parameter.name.startswith("MIDI Ch. ")
        and parameter.name not in {"Buffer Size", "Sample Rate"}
    }


def _stereo_output(rendered, frames: int) -> np.ndarray:
    output = np.asarray(rendered, dtype=np.float32)
    if output.ndim == 1:
        output = output.reshape(1, -1)
    if output.ndim != 2:
        raise PluginError("Plugin chain returned invalid audio dimensions")
    if output.shape[0] == frames and output.shape[1] != frames:
        output = output.T
    if output.shape[1] != frames:
        raise PluginError("Plugin chain returned the wrong frame count")
    if output.shape[0] == 1:
        output = np.repeat(output, 2, axis=0)
    elif output.shape[0] >= 2:
        # Auxiliary/surround outputs never leak into the main stereo bus.
        output = output[:2]
    if output.shape != (2, frames) or not np.isfinite(output).all():
        raise PluginError("Plugin chain returned invalid or incomplete stereo audio")
    return np.ascontiguousarray(output, dtype=np.float32)


def _supports_stereo_aux(plugin, sample_rate: int) -> bool:
    """Probe whether the backend exposes an extra stereo input pair.

    Pedalboard exposes plugin buses as channel layouts rather than a separate
    sidechain method. A successful four-channel process is therefore required
    before the project is allowed to route channels 3/4 as auxiliary audio.
    """
    probe = np.zeros((4, 32), dtype=np.float32)
    try:
        rendered = plugin.process(probe, sample_rate, buffer_size=32, reset=True)
        _stereo_output(rendered, 32)
        plugin.reset()
        return True
    except (ValueError, RuntimeError, TypeError):
        try:
            plugin.reset()
        except Exception:
            pass
        return False


def plugin_chain_worker(connection, specifications, sample_rate):
    """Load and process one serial effect chain in a disposable child."""
    try:
        if (
            not isinstance(specifications, list)
            or not 1 <= len(specifications) <= MAX_CHAIN_PLUGINS
        ):
            raise PluginError(f"Plugin chain must contain 1–{MAX_CHAIN_PLUGINS} effects")
        if any(not isinstance(item, dict) for item in specifications):
            raise PluginError("Plugin chain entries must be objects")

        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)

        import pedalboard_native

        def initialize_parameters(self, parameter_values):
            if parameter_values:
                raise PluginError("Unexpected constructor parameters")

        pedalboard_native.ExternalPlugin.__set_initial_parameter_values__ = initialize_parameters

        plugins = []
        channels = []
        aux_capable = []
        infos = []
        total_latency = 0
        for specification in specifications:
            path = str(specification.get("path", ""))
            suffix = Path(path).suffix.casefold()
            if suffix == ".component":
                plugin_class = pedalboard_native.AudioUnitPlugin
            elif suffix == ".vst3":
                plugin_class = pedalboard_native.VST3Plugin
            else:
                raise PluginError("Effect chain currently hosts VST3 and Audio Unit plugins")
            plugin = plugin_class(
                path,
                plugin_name=specification.get("plugin_name") or None,
                initialization_timeout=5,
            )
            if not plugin.is_effect:
                raise PluginError(f"Plugin is not an audio effect: {plugin.name}")
            if specification.get("state"):
                state = base64.b64decode(specification["state"], validate=True)
                if len(state) > MAX_STATE:
                    raise PluginError("Plugin state is too large")
                plugin.raw_state = state
            parameters = _plugin_parameters(plugin)
            for name, value in specification.get("parameters", {}).items():
                if name not in parameters:
                    raise PluginError(f"Plugin parameter is missing: {name}")
                parameters[name].raw_value = float(value)
            state = plugin.raw_state
            if len(state) > MAX_STATE:
                raise PluginError("Plugin state exceeds the 2 MiB project limit")
            latency = max(0, int(plugin.reported_latency_samples))
            total_latency += latency
            sidechain_audio = _supports_stereo_aux(plugin, sample_rate)
            infos.append(
                {
                    "name": str(plugin.name),
                    "effect": True,
                    "instrument": bool(plugin.is_instrument),
                    "latency_samples": latency,
                    "sidechain_audio": sidechain_audio,
                    "state": base64.b64encode(state).decode(),
                    "parameters": {
                        name: {
                            "name": str(parameter.name),
                            "value": float(parameter.raw_value),
                            "display": str(parameter.string_value),
                        }
                        for name, parameter in list(parameters.items())[:512]
                    },
                }
            )
            plugins.append(plugin)
            channels.append(2)
            aux_capable.append(sidechain_audio)

        send_packet(
            connection,
            {
                "name": " → ".join(item["name"] for item in infos),
                "effect": True,
                "instrument": False,
                "latency_samples": total_latency,
                "sidechain_slots": [index for index, capable in enumerate(aux_capable) if capable],
                "chain": infos,
            },
        )

        while True:
            request, raw = receive_packet(connection)
            if request.get("command") == "close":
                break
            frames = request.get("frames", 0)
            if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
                raise PluginError("Invalid plugin-chain audio block")
            input_channels = request.get("input_channels", 2)
            slots = request.get("sidechain_slots", [])
            if (
                type(input_channels) is not int
                or input_channels < 2
                or input_channels > 2 + 2 * MAX_CHAIN_PLUGINS
                or not isinstance(slots, list)
                or len(slots) > MAX_CHAIN_PLUGINS
                or any(type(slot) is not int or not 0 <= slot < len(plugins) for slot in slots)
                or input_channels != 2 + 2 * len(slots)
            ):
                raise PluginError("Invalid plugin-chain auxiliary routing")
            if len(raw) != frames * input_channels * 4:
                raise PluginError("Invalid plugin-chain input buffer")
            if request.get("reset"):
                for plugin in plugins:
                    plugin.reset()

            packed = np.frombuffer(raw, dtype="<f4").reshape(frames, input_channels)
            current = np.ascontiguousarray(packed[:, :2].T, dtype=np.float32)
            aux = {
                slot: np.ascontiguousarray(packed[:, 2 + offset * 2 : 4 + offset * 2].T)
                for offset, slot in enumerate(slots)
            }
            for index, plugin in enumerate(plugins):
                incoming = current if channels[index] == 2 else current.mean(axis=0, keepdims=True)
                sidechain = aux.get(index)
                if sidechain is not None:
                    if not aux_capable[index]:
                        raise PluginError(
                            f"{plugin.name} does not expose an auxiliary stereo input through this host"
                        )
                    incoming = np.ascontiguousarray(np.vstack((current, sidechain)), dtype=np.float32)
                try:
                    rendered = plugin.process(
                        incoming,
                        sample_rate,
                        buffer_size=frames,
                        reset=False,
                    )
                except ValueError as exc:
                    if sidechain is not None:
                        raise PluginError(
                            f"{plugin.name} rejected its configured auxiliary audio input"
                        ) from exc
                    if channels[index] != 2 or "does not support 2-channel" not in str(exc):
                        raise
                    channels[index] = 1
                    rendered = plugin.process(
                        current.mean(axis=0, keepdims=True),
                        sample_rate,
                        buffer_size=frames,
                        reset=False,
                    )
                current = _stereo_output(rendered, frames)
            send_packet(connection, {"frames": frames}, current.T)
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            send_packet(connection, {"error": str(exc)[:2000] or type(exc).__name__})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class IsolatedPluginChain:
    """Synchronous parent adapter for one isolated serial effect chain."""

    def __init__(
        self,
        specifications,
        sample_rate=48000,
        *,
        worker=plugin_chain_worker,
        timeout=20,
    ):
        specs = list(specifications)
        if not 1 <= len(specs) <= MAX_CHAIN_PLUGINS:
            raise PluginError(f"Plugin chain must contain 1–{MAX_CHAIN_PLUGINS} effects")
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=worker,
            args=(child, specs, sample_rate),
            daemon=True,
            name="Anharmonic isolated plugin chain",
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
            raise PluginError("Plugin chain stopped responding")
        try:
            header, raw = receive_packet(self.connection)
        except (EOFError, OSError, ValueError) as exc:
            raise PluginError("Plugin chain process closed unexpectedly") from exc
        if header.get("error"):
            raise PluginError(header["error"])
        return header, raw

    def render(self, audio, frames, midi=(), *, reset=False, timeout=2, sidechains=None):
        del midi
        if self.closed:
            raise PluginError("Plugin chain is closed")
        if audio is None:
            raise PluginError("Effect chain requires stereo audio input")
        try:
            source = np.asarray(audio, dtype=np.float32)
            if source.shape != (frames, 2):
                raise PluginError("Effect chain requires frame-major stereo audio")
            items = sorted((int(slot), block) for slot, block in (sidechains or {}).items())
            if len(items) > MAX_CHAIN_PLUGINS:
                raise PluginError("Too many sidechain inputs")
            packed = np.empty((frames, 2 + 2 * len(items)), dtype=np.float32)
            packed[:, :2] = source
            for offset, (slot, block) in enumerate(items):
                if slot not in self.info.get("sidechain_slots", []):
                    raise PluginError(f"Plugin slot {slot + 1} does not expose auxiliary audio")
                sidechain = np.asarray(block, dtype=np.float32)
                if sidechain.shape != (frames, 2):
                    raise PluginError("Sidechain input must be frame-major stereo audio")
                packed[:, 2 + offset * 2 : 4 + offset * 2] = sidechain
            send_packet(
                self.connection,
                {
                    "frames": frames,
                    "reset": reset,
                    "input_channels": packed.shape[1],
                    "sidechain_slots": [slot for slot, _block in items],
                },
                packed,
            )
            header, raw = self._receive(timeout)
            if header.get("frames") != frames or len(raw) != frames * 8:
                raise PluginError("Plugin chain returned the wrong block length")
            result = np.frombuffer(raw, dtype="<f4").reshape(frames, 2)
            if not np.isfinite(result).all():
                raise PluginError("Plugin chain returned nonfinite audio")
            return result
        except PluginError:
            self.close()
            raise
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.close()
            raise PluginError("Plugin chain process closed unexpectedly") from exc

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


def live_plugin_chain(specifications, sample_rate: int, blocksize: int):
    """Create one nonblocking live bridge around an entire insert chain."""
    from .sidechain import SidechainLivePlugin

    plugin = IsolatedPluginChain(specifications, sample_rate)
    try:
        if plugin.info.get("sidechain_slots"):
            return SidechainLivePlugin(plugin, blocksize)
        return LivePlugin(plugin, blocksize)
    except Exception:
        plugin.close()
        raise
