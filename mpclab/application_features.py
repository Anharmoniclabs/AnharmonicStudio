"""One feature-installation path for production startup and packaged self-checks."""


def install_application_runtime():
    """Install persistence and engine hooks before constructing a project/window."""
    from .automation_mode_state import install_automation_mode_state
    from .plugin_chain_runtime import install_plugin_chain_runtime
    from .premium_workflows import install_premium_runtime
    from .pro_daw_state import install_pro_daw_state
    from .recording_workflows import install_recording_capture_extensions
    from .timeline_markers import install_timeline_marker_state

    install_premium_runtime()
    install_pro_daw_state()
    install_automation_mode_state()
    install_plugin_chain_runtime()
    install_recording_capture_extensions()
    install_timeline_marker_state()


def attach_application_features(window):
    """Attach the same commands and UI controllers to every application window."""
    from .automation_modes import attach_automation_modes
    from .plugin_chain_ui import attach_plugin_chain_ui
    from .premium_workflows import attach_premium_workflows
    from .recording_workflows import attach_recording_workflows
    from .routing_ui import attach_routing_ui
    from .take_comping import attach_take_comping
    from .workflow_compat import restore_unmanaged_legacy_shortcuts
    from .ui.track_management import attach_track_management
    from .ui.timeline_markers import attach_timeline_markers
    from .ui.audio_analysis import attach_audio_analysis

    controller = attach_premium_workflows(window)
    attach_routing_ui(window, controller)
    attach_plugin_chain_ui(window, controller)
    attach_recording_workflows(window, controller)
    attach_take_comping(window, controller)
    attach_automation_modes(window, controller)
    attach_track_management(window, controller)
    attach_timeline_markers(window, controller)
    attach_audio_analysis(window, controller)
    restore_unmanaged_legacy_shortcuts(controller)
    return controller
