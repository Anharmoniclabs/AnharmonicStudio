/* GPL-3.0-or-later realtime control smoothing kernels.
 * No allocation, locks, I/O, or Python API calls.
 */
#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define MPC_EXPORT extern "C" __declspec(dllexport)
#else
#define MPC_EXPORT extern "C"
#endif

/* state: current, target, step, remaining-frames. */
MPC_EXPORT void mpc_control_linear(float *out, size_t frames, double *state) {
    double current = state[0];
    const double target = state[1];
    double step = state[2];
    int64_t remaining = (int64_t)state[3];
    for (size_t i = 0; i < frames; ++i) {
        if (remaining > 0) {
            current += step;
            --remaining;
            if (remaining == 0) {
                current = target;
                step = 0.0;
            }
        }
        out[i] = (float)current;
    }
    state[0] = current;
    state[2] = step;
    state[3] = (double)remaining;
}

/* state: current, target, coefficient. */
MPC_EXPORT void mpc_control_onepole(float *out, size_t frames, double *state) {
    double current = state[0];
    const double target = state[1];
    const double coefficient = state[2];
    for (size_t i = 0; i < frames; ++i) {
        if (coefficient == 0.0)
            current = target;
        else
            current = target + coefficient * (current - target);
        out[i] = (float)current;
    }
    state[0] = current;
}
