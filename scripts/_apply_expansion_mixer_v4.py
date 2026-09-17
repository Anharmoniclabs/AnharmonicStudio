from pathlib import Path
import runpy

runpy.run_path("scripts/_apply_expansion_mixer_v3.py", run_name="__main__")
path = Path("mpclab/expansion_instruments.py")
text = path.read_text()
if text.count("import math\n") != 1:
    raise SystemExit("instrument math import anchor changed")
path.write_text(text.replace("import math\n", "", 1))
