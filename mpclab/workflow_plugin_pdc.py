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

    def refresh(controller, slot: str | None = None) -> int:
        engine = controller.app.engine
        prepare = getattr(engine, "prepare_plugin_latency", None)
        delay = prepare() if prepare is not None else 0
        if slot is not None:
            plugin = getattr(engine.external, slot, None)
            if plugin is not None and not getattr(plugin, "error", ""):
                samples = plugin_path_latency_samples(plugin, include_live_bridge=True)
                ms = samples / engine.sr * 1000.0
                if slot == "instrument":
                    controller.plugin_status = (
                        f"{plugin.info.get('name', 'Instrument')} ready · "
                        f"{ms:.1f} ms isolated path · PDC {delay / engine.sr * 1000.0:.1f} ms"
                    )
                else:
                    controller.plugin_status = (
                        f"{plugin.info.get('name', 'Effect')} ready · {ms:.1f} ms master path"
                    )
        return delay

    def plugin_loaded(controller, generation, slot, bridge, saved, error):
        result = original_loaded(controller, generation, slot, bridge, saved, error)
        if not error and getattr(controller.app.engine.external, slot, None) is bridge:
            refresh(controller, slot)
            controller.changed.emit()
        return result

    def remove_plugin(controller, slot):
        result = original_remove(controller, slot)
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
