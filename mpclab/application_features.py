"""One feature-installation path for production startup and packaged self-checks."""


def install_application_runtime():
    """Install persistence and engine hooks before constructing a project/window."""
    from .automation_mode_state import install_automation_mode_state
    from .mastering_runtime import install_mastering_runtime
    from .plugin_chain_runtime import install_plugin_chain_runtime
    from .premium_workflows import install_premium_runtime
    from .pro_daw_state import install_pro_daw_state
    from .recording_workflows import install_recording_capture_extensions
    from .timeline_markers import install_timeline_marker_state
    from .workflow_organization import install_organization_state

    install_premium_runtime()
    install_organization_state()
    install_pro_daw_state()
    install_automation_mode_state()
    install_plugin_chain_runtime()
    install_recording_capture_extensions()
    install_mastering_runtime()
    install_timeline_marker_state()


def attach_application_features(window):
    """Attach the same commands and UI controllers to every application window."""
    from .automation_clipboard import attach_automation_clipboard
    from .automation_modes import attach_automation_modes
    from .dawproject_ui import attach_dawproject_interchange
    from .plugin_chain_ui import attach_plugin_chain_ui
    from .premium_workflows import attach_premium_workflows
    from .recording_workflows import attach_recording_workflows
    from .routing_ui import attach_routing_ui
    from .take_comping import attach_take_comping
    from .ui.mastering import attach_mastering_workspace
    from .ui.timeline_markers import attach_timeline_markers
    from .workflow_compat import restore_unmanaged_legacy_shortcuts
    from .workflow_organization import attach_organization_workflows

    controller = attach_premium_workflows(window)
    attach_routing_ui(window, controller)
    attach_plugin_chain_ui(window, controller)
    attach_recording_workflows(window, controller)
    attach_take_comping(window, controller)
    attach_automation_modes(window, controller)
    attach_automation_clipboard(window, controller)
    attach_mastering_workspace(window, controller)
    attach_organization_workflows(window, controller)
    attach_dawproject_interchange(window, controller)
    attach_timeline_markers(window, controller)
    restore_unmanaged_legacy_shortcuts(controller)
    return controller
