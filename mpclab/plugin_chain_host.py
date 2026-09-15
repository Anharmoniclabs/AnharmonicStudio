"""Isolated VST3/Audio Unit effect chains with one live bridge per chain.

Every chain lives in one disposable child process.  Third-party code never runs
in the audio callback and a chain of several inserts pays the existing two-block
bridge once, rather than once per plugin.  Offline callers use the same child
synchronously and therefore add only plugin-reported intrinsic latency.
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
    if output.shape == (frames, 1):
        output = output.T
    elif output.shape == (frames, 2):
        output = output.T
    if output.shape == (1, frames):
        output = np.repeat(output, 2, axis=0)
    if output.shape != (2, frames) or not np.isfinite(output).all():
        raise PluginError("Plugin chain returned invalid or incomplete stereo audio")
    return np.ascontiguousarray(output, dtype=np.float32)


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

        # Native plugins may write directly to C stdout/stderr.  Keep that noise
        # out of the parent/export protocol; failures still cross the bounded IPC.
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
            infos.append(
                {
                    "name": str(plugin.name),
                    "effect": True,
                    "instrument": bool(plugin.is_instrument),
                    "latency_samples": latency,
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

        send_packet(
            connection,
            {
                "name": " → ".join(item["name"] for item in infos),
                "effect": True,
                "instrument": False,
                "latency_samples": total_latency,
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
            if len(raw) != frames * 2 * 4:
                raise PluginError("Invalid plugin-chain input buffer")
            if request.get("reset"):
                for plugin in plugins:
                    plugin.reset()

            current = np.frombuffer(raw, dtype="<f4").reshape(frames, 2).T
            current = np.ascontiguousarray(current, dtype=np.float32)
            for index, plugin in enumerate(plugins):
                incoming = current if channels[index] == 2 else current.mean(axis=0, keepdims=True)
                try:
                    rendered = plugin.process(
                        incoming,
                        sample_rate,
                        buffer_size=frames,
                        reset=False,
                    )
                except ValueError as exc:
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
    """Synchronous parent-side adapter for one isolated serial effect chain."""

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

    def render(self, audio, frames, midi=(), *, reset=False, timeout=2):
        if self.closed:
            raise PluginError("Plugin chain is closed")
        if audio is None:
            raise PluginError("Effect chain requires stereo audio input")
        try:
            send_packet(self.connection, {"frames": frames, "reset": reset}, audio)
            header, raw = self._receive(timeout)
            if header.get("frames") != frames or len(raw) != frames * 8:
                raise PluginError("Plugin chain returned the wrong block length")
            result = np.frombuffer(raw, dtype="<f4").reshape(frames, 2)
            if not np.isfinite(result).all():
                raise PluginError("Plugin chain returned nonfinite audio")
            return result
        except (EOFError, BrokenPipeError, OSError) as exc:
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


def live_plugin_chain(specifications, sample_rate: int, blocksize: int) -> LivePlugin:
    """Create one nonblocking live bridge around an entire insert chain."""
    plugin = IsolatedPluginChain(specifications, sample_rate)
    try:
        return LivePlugin(plugin, blocksize)
    except Exception:
        plugin.close()
        raise
