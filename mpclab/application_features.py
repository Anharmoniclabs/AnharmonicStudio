"""One feature-installation path for production startup and packaged self-checks."""


def install_application_runtime():
    """Install persistence and engine hooks before constructing a project/window."""
    from .automation_mode_state import install_automation_mode_state
    from .expansion_instrument_runtime import install_expansion_instrument_runtime
    from .fx_unification import install_fx_unification
    from .mastering_runtime import install_mastering_runtime
    from .midi_file_state import install_midi_file_state
    from .plugin_chain_runtime import install_plugin_chain_runtime
    from .premium_workflows import install_premium_runtime
    from .pro_audio_runtime import install_pro_audio_runtime
    from .pro_daw_state import install_pro_daw_state
    from .processor_runtime import install_processor_runtime
    from .profiler_runtime import install_profiler_runtime
    from .project_audio_runtime import install_project_audio_runtime
    from .read_ahead_runtime import install_read_ahead_runtime
    from .recording_workflows import install_recording_capture_extensions
    from .sidechain_runtime import install_sidechain_runtime
    from .timeline_markers import install_timeline_marker_state
    from .workflow_organization import install_organization_state

    # Shared primitives must be published before any Engine creates MixRack.
    install_fx_unification()
    install_premium_runtime()
    install_organization_state()
    install_pro_daw_state()
    install_automation_mode_state()
    install_plugin_chain_runtime()
    install_sidechain_runtime()
    # First-party engine bridges are wrapped by project-audio start so a rate
    # change closes stale-rate paths, then rebuilds them before PortAudio starts.
    install_expansion_instrument_runtime()
    install_project_audio_runtime()
    install_read_ahead_runtime()
    install_processor_runtime()
    install_profiler_runtime()
    install_pro_audio_runtime()
    install_recording_capture_extensions()
    install_mastering_runtime()
    install_timeline_marker_state()
    install_midi_file_state()


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
    from .ui.midi_files import attach_midi_files
    from .ui.instruments import attach_instruments
    from .ui.loudness_delivery import attach_loudness_delivery
    from .ui.timeline_markers import attach_timeline_markers
    from .ui.track_management import attach_track_management
    from .ui.daw_expansion import attach_daw_expansion
    from .workflow_compat import restore_unmanaged_legacy_shortcuts
    from .ui.audio_analysis import attach_audio_analysis
    from .workflow_organization import attach_organization_workflows

    controller = attach_premium_workflows(window)
    attach_routing_ui(window, controller)
    attach_plugin_chain_ui(window, controller)
    attach_recording_workflows(window, controller)
    attach_take_comping(window, controller)
    attach_automation_modes(window, controller)
    attach_track_management(window, controller)
    attach_timeline_markers(window, controller)
    attach_audio_analysis(window, controller)
    attach_midi_files(window, controller)
    attach_instruments(window, controller)
    attach_loudness_delivery(window, controller)
    attach_automation_clipboard(window, controller)
    attach_mastering_workspace(window, controller)
    attach_organization_workflows(window, controller)
    attach_dawproject_interchange(window, controller)
    attach_daw_expansion(window, controller)
    restore_unmanaged_legacy_shortcuts(controller)
    return controller
