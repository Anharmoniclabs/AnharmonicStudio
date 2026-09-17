from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/daw_expansion_state.py",
    '''    if type(result["pre_fader"]) is not bool or type(result["enabled"]) is not bool:\n        raise ValueError("sidechain enabled/pre_fader fields must be booleans")\n    if project is not None:\n''',
    '''    if type(result["pre_fader"]) is not bool or type(result["enabled"]) is not bool:\n        raise ValueError("sidechain enabled/pre_fader fields must be booleans")\n    if not result["source"].startswith("track:"):\n        raise ValueError("sidechain source must be a mixer track with a realtime audio tap")\n    if result["source"] == result["target"]:\n        raise ValueError("a track cannot sidechain a plugin on itself")\n    if project is not None:\n''',
    "sidechain audio-tap contract",
)

replace_once(
    "mpclab/daw_expansion_state.py",
    '''    if len({item["id"] for item in result["sidechains"]}) != len(result["sidechains"]):\n        raise ValueError("sidechain IDs must be unique")\n\n    channels = value.get("track_channels", {})\n''',
    '''    if len({item["id"] for item in result["sidechains"]}) != len(result["sidechains"]):\n        raise ValueError("sidechain IDs must be unique")\n\n    # Sidechain track dependencies are part of callback scheduling. Reject\n    # cycles at project-validation time instead of discovering them when audio\n    # starts. Bus/master targets terminate this dependency graph.\n    track_nodes = {\n        f"track:{track.id}" for track in project.tracks\n    } if project is not None else {\n        item["source"] for item in result["sidechains"]\n    } | {\n        item["target"] for item in result["sidechains"] if item["target"].startswith("track:")\n    }\n    edges = {node: set() for node in track_nodes}\n    indegree = dict.fromkeys(track_nodes, 0)\n    for item in result["sidechains"]:\n        if not item["enabled"] or not item["target"].startswith("track:"):\n            continue\n        source, target = item["source"], item["target"]\n        if source in edges and target in indegree and target not in edges[source]:\n            edges[source].add(target)\n            indegree[target] += 1\n    ready = sorted(node for node, degree in indegree.items() if degree == 0)\n    visited = 0\n    while ready:\n        source = ready.pop(0)\n        visited += 1\n        for target in sorted(edges[source]):\n            indegree[target] -= 1\n            if indegree[target] == 0:\n                ready.append(target)\n                ready.sort()\n    if visited != len(track_nodes):\n        raise ValueError("track sidechain routes must form an acyclic graph")\n\n    channels = value.get("track_channels", {})\n''',
    "sidechain dependency cycle validation",
)
