from pathlib import Path

source = Path("scripts/_apply_expansion_mixer.py").read_text()
source = source.replace(
    '''    master = engine._master[:frames]\\n    master.fill(0.0)\\n    preview_bus = engine._preview_bus[:frames]\\n''',
    '''    master = engine._master[:frames]\\n    master.fill(0.0)\\n    preview_bus = engine._preview[:frames]\\n''',
)
source = source.replace(
    '''    finally:\\n        if chains is not None:\\n            chains.close()\\n        engine.mode = saved_mode\\n''',
    '''    finally:\\n        if plugins is not None:\\n            plugins.close()\\n        if chains is not None:\\n            chains.close()\\n        engine.mode = saved_mode\\n''',
)
source = source.replace(
    '''    finally:\\n        if chains is not None:\\n            chains.close()\\n        if owned is not None:\\n            owned.close()\\n        engine.mode = saved_mode\\n''',
    '''    finally:\\n        if plugins is not None:\\n            plugins.close()\\n        if chains is not None:\\n            chains.close()\\n        if owned is not None:\\n            owned.close()\\n        engine.mode = saved_mode\\n''',
)
exec(compile(source, "scripts/_apply_expansion_mixer.py", "exec"))
