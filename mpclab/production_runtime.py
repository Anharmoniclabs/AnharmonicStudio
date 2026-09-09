"""CachyOS-friendly, application-scoped runtime policy before DSP imports."""

import ctypes
import os
import sys


def configure():
    # Tiny callback matrices must not wake a competing BLAS thread pool.
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    sys.setswitchinterval(0.001)
    if not sys.platform.startswith("linux"):
        return
    try:
        # Set only this newly launched DAW thread's name, enabling exact
        # ananicy rules instead of a dangerous machine-wide python rule.
        libc = ctypes.CDLL(None)
        libc.prctl(15, ctypes.c_char_p(b"anharmonic-daw"), 0, 0, 0)
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current > -4:
            # Same niceness as CachyOS's Player-Audio class. FIFO remains
            # limited to the actual PortAudio callback in linux_audio.py.
            os.setpriority(os.PRIO_PROCESS, 0, -4)
    except (AttributeError, OSError):
        pass
