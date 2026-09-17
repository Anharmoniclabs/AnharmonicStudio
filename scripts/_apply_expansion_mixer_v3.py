from pathlib import Path

source = Path("scripts/_apply_expansion_mixer_v2.py").read_text()
# v2 executes the base script. Patch the base-script source transform so the
# post-fader anchor is unique to the already-modified bounded renderer.
base = Path("scripts/_apply_expansion_mixer.py").read_text()
old_literal = ''''''                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\\n                route_track(\\n''''''
new_old_literal = ''''''                sidechains.capture(f"track:{track.id}", buf, frames, pre_fader=True)\\n                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\\n                route_track(\\n''''''
old_replacement = ''''''                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\\n                sidechains.capture(\\n                    f"track:{track.id}", panned[:frames], frames, pre_fader=False\\n                )\\n                route_track(\\n''''''
new_replacement = ''''''                sidechains.capture(f"track:{track.id}", buf, frames, pre_fader=True)\\n                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\\n                sidechains.capture(\\n                    f"track:{track.id}", panned[:frames], frames, pre_fader=False\\n                )\\n                route_track(\\n''''''
if base.count(old_literal) != 1 or base.count(old_replacement) != 1:
    raise SystemExit("v3 source anchors changed")
base = base.replace(old_literal, new_old_literal, 1).replace(old_replacement, new_replacement, 1)
Path("scripts/_apply_expansion_mixer.py").write_text(base)
exec(compile(source, "scripts/_apply_expansion_mixer_v2.py", "exec"))
