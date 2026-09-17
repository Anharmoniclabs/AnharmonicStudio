"""Synchronize isolated-plugin latency compensation with the Devices controller."""

from __future__ import annotations

from .plugin_latency import plugin_path_latency_samples

_INSTALLED = False


def install_device_pdc_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .ui.devices import DevicesController

    original_loaded = DevicesController._plugin_loaded
    original_remove = DevicesController.remove_plugin
    original_sync = DevicesController.sync_project

    def refresh(controller, slot: str | None = None, instrument_id=None) -> int:
        engine = controller.app.engine
        prepare = getattr(engine, "prepare_plugin_latency", None)
        delay = prepare() if prepare is not None else 0
        if slot is not None:
            plugin = (
                engine.external.instrument_for(instrument_id)
                if slot == "instrument"
                else getattr(engine.external, slot, None)
            )
            if plugin is not None and not getattr(plugin, "error", ""):
                samples = plugin_path_latency_samples(plugin, include_live_bridge=True)
                ms = samples / engine.sr * 1000.0
                if slot == "instrument":
                    label = plugin.info.get("name", "Instrument")
                    if instrument_id is not None:
                        label += f" · {controller.app.project.instrument(instrument_id).name}"
                    controller.plugin_status = (
                        f"{label} ready · {ms:.1f} ms isolated path · "
                        f"PDC {delay / engine.sr * 1000.0:.1f} ms"
                    )
                else:
                    controller.plugin_status = (
                        f"{plugin.info.get('name', 'Effect')} ready · {ms:.1f} ms master path"
                    )
        return delay

    def plugin_loaded(controller, generation, slot, bridge, saved, error):
        result = original_loaded(controller, generation, slot, bridge, saved, error)
        instrument_id = saved[2] if len(saved) > 2 else None
        active = (
            controller.app.engine.external.instrument_for(instrument_id)
            if slot == "instrument"
            else getattr(controller.app.engine.external, slot, None)
        )
        if not error and active is bridge:
            refresh(controller, slot, instrument_id)
            controller.changed.emit()
        return result

    def remove_plugin(controller, slot, instrument_id=None):
        result = original_remove(controller, slot, instrument_id)
        refresh(controller)
        return result

    def sync_project(controller):
        result = original_sync(controller)
        refresh(controller)
        return result

    DevicesController._plugin_loaded = plugin_loaded
    DevicesController.remove_plugin = remove_plugin
    DevicesController.sync_project = sync_project
    _INSTALLED = True
