/* GPL-3.0-or-later hot loops. No allocation, locks, I/O, or Python API calls. */
#include <stddef.h>
#include <stdint.h>
#include <math.h>

#ifdef _WIN32
#define MPC_EXPORT __declspec(dllexport)
#else
#define MPC_EXPORT
#endif

/* Recurrence replaces FFT convolution in compressor/reverb smoothing. */
MPC_EXPORT void mpc_onepole(float *block, size_t frames, size_t channels,
                 float *state, double b) {
    for (size_t c = 0; c < channels; ++c) {
        double value = state[c];
        for (size_t i = 0; i < frames; ++i) {
            size_t at = i * channels + c;
            value = (1.0 - b) * block[at] + b * value;
            block[at] = (float)value;
        }
        state[c] = (float)value;
    }
}

MPC_EXPORT int mpc_dsp_abi(void) { return 1; }

#define PI 3.14159265358979323846264338327950288

static double blep(double phase, double step) {
    double out = 0.0;
    if (phase < step) {
        double x = phase / step;
        out = x + x - x * x - 1.0;
    }
    if (phase > 1.0 - step) {
        double x = (phase - 1.0) / step;
        out = x * x + x + x + 1.0;
    }
    return out;
}

static double oscillator(int kind, double phase, double step, double pw) {
    if (kind == 1) return sin(2.0 * PI * phase);
    if (kind == 2) return 1.0 - 4.0 * fabs(phase - 0.5);
    if (kind == 3) {
        double shifted = phase - pw;
        if (shifted < 0.0) shifted += 1.0;
        return (phase < pw ? 1.0 : -1.0) + blep(phase, step) - blep(shifted, step);
    }
    return 2.0 * phase - 1.0 - blep(phase, step);
}

/* params: sr, f1, f2, lfo_rate, attack_inc, decay_dec, sustain,
 * release_frames, osc1, osc2, mix, spread, pulse_width, sub, noise,
 * lfo_pitch, lfo_filter, filter_env, cutoff, resonance_k, drive, gain, norm.
 * state: 3 oscillator phases, lfo phase, envelope, stage, release step,
 * dead, and four filter integrators. Noise comes from the reference RNG.
 * Half-rate filtering and pair hold match the existing patch sound exactly.
 */
MPC_EXPORT void mpc_synth(float *out, size_t n, const double *noise, const double *p,
               double *s, int64_t age, int64_t gate) {
    double sr = p[0], sums[3] = {0.0, 0.0, 0.0};
    double env = s[4], release = s[6];
    int stage = (int)s[5], dead = (int)s[7];
    for (size_t start = 0; start < n; start += 2) {
        size_t take = n - start < 2 ? 1 : 2;
        double left = 0.0, right = 0.0, envelopes[2], first_lfo = 0.0;
        for (size_t j = 0; j < take; ++j) {
            size_t i = start + j;
            double lfo_phase = fmod(s[3] + (double)i * p[3] / sr, 1.0);
            double lfo = sin(2.0 * PI * lfo_phase);
            if (j == 0) first_lfo = lfo;
            double pitch = exp2(lfo * p[15] / 1200.0);
            double steps[3] = {fmin(0.45, p[1] * pitch / sr),
                               fmin(0.45, p[2] * pitch / sr),
                               fmin(0.45, p[1] * 0.5 * pitch / sr)};
            double phases[3];
            for (int c = 0; c < 3; ++c) {
                phases[c] = fmod(s[c] + sums[c], 1.0);
                sums[c] += steps[c];
            }
            double osc1 = oscillator((int)p[8], phases[0], steps[0], p[12]);
            double osc2 = oscillator((int)p[9], phases[1], steps[1], p[12]);
            double sub = sin(2.0 * PI * phases[2]);
            left += ((osc1 * (1.0 - p[10]) * (1.0 + p[11]) +
                      osc2 * p[10] * (1.0 - p[11])) +
                     (sub * p[13] + noise[i] * p[14])) * p[22];
            right += ((osc1 * (1.0 - p[10]) * (1.0 - p[11]) +
                       osc2 * p[10] * (1.0 + p[11])) +
                      (sub * p[13] + noise[n - 1 - i] * p[14])) * p[22];
            if (gate >= 0 && age + (int64_t)i >= gate && stage != 3 && !dead) {
                stage = 3; release = fmax(1e-9, env / p[7]);
            }
            if (stage == 0) {
                env += p[4];
                if (env >= 1.0) { env = 1.0; stage = 1; }
            } else if (stage == 1) {
                env -= p[5];
                if (env <= p[6]) { env = p[6]; stage = 2; }
            } else if (stage == 2) {
                env = p[6];
            } else {
                env = fmax(0.0, env - release);
                if (env <= 0.0) dead = 1;
            }
            envelopes[j] = env;
        }
        left = tanh(left / (double)take * p[20]);
        right = tanh(right / (double)take * p[20]);
        double filter_rate = sr * 0.5;
        double cutoff = p[18] * exp2(p[17] * envelopes[0] * 4.5 + p[16] * first_lfo * 3.0);
        cutoff = fmin(filter_rate * 0.44, fmax(30.0, cutoff));
        double blend = fmin(1.0, fmax(0.0, (cutoff - filter_rate * 0.28) / (filter_rate * 0.16)));
        double g = tan(PI * cutoff / filter_rate);
        double a1 = 1.0 / (1.0 + g * (g + p[19]));
        double a2 = g * a1, a3 = g * a2;
        double v3 = left - s[9], v1 = a1 * s[8] + a2 * v3;
        double v2_l = s[9] + a2 * s[8] + a3 * v3;
        s[8] = 2.0 * v1 - s[8]; s[9] = 2.0 * v2_l - s[9];
        v3 = right - s[11]; v1 = a1 * s[10] + a2 * v3;
        double v2_r = s[11] + a2 * s[10] + a3 * v3;
        s[10] = 2.0 * v1 - s[10]; s[11] = 2.0 * v2_r - s[11];
        double filtered_l = v2_l * (1.0 - blend) + left * blend;
        double filtered_r = v2_r * (1.0 - blend) + right * blend;
        for (size_t j = 0; j < take; ++j) {
            out[(start + j) * 2] += (float)(filtered_l * envelopes[j] * p[21]);
            out[(start + j) * 2 + 1] += (float)(filtered_r * envelopes[j] * p[21]);
        }
    }
    for (int c = 0; c < 3; ++c) s[c] = fmod(s[c] + sums[c], 1.0);
    s[3] = fmod(s[3] + (double)n * p[3] / sr, 1.0);
    s[4] = env; s[5] = stage; s[6] = release; s[7] = dead;
}

/* state: envelope, stage (attack/decay/sustain/release), release step, dead */
MPC_EXPORT void mpc_envelope(double *out, size_t n, double *state, int64_t age,
                  int64_t gate, double attack, double decay, double sustain,
                  int64_t release_frames) {
    double env = state[0], release = state[2];
    int stage = (int)state[1], dead = (int)state[3];
    for (size_t i = 0; i < n; ++i) {
        if (gate >= 0 && age + (int64_t)i >= gate && stage != 3 && !dead) {
            stage = 3;
            release = fmax(1e-9, env / (double)release_frames);
        }
        if (stage == 0) {
            env += attack;
            if (env >= 1.0) { env = 1.0; stage = 1; }
        } else if (stage == 1) {
            env -= decay;
            if (env <= sustain) { env = sustain; stage = 2; }
        } else if (stage == 2) {
            env = sustain;
        } else {
            env = fmax(0.0, env - release);
            if (env <= 0.0) dead = 1;
        }
        out[i] = env;
    }
    state[0] = env; state[1] = stage; state[2] = release; state[3] = dead;
}

/* The exact existing nonlinear-filter recurrence, with stereo state retained. */
MPC_EXPORT void mpc_filter(const double *left, const double *right, const double *g,
                size_t n, double k, double *state, double *out_l, double *out_r) {
    double ic1_l = state[0], ic2_l = state[1], ic1_r = state[2], ic2_r = state[3];
    for (size_t i = 0; i < n; ++i) {
        double a1 = 1.0 / (1.0 + g[i] * (g[i] + k));
        double a2 = g[i] * a1, a3 = g[i] * a2;
        double v3 = left[i] - ic2_l;
        double v1 = a1 * ic1_l + a2 * v3;
        double v2 = ic2_l + a2 * ic1_l + a3 * v3;
        ic1_l = 2.0 * v1 - ic1_l;
        ic2_l = 2.0 * v2 - ic2_l;
        out_l[i] = v2;
        v3 = right[i] - ic2_r;
        v1 = a1 * ic1_r + a2 * v3;
        v2 = ic2_r + a2 * ic1_r + a3 * v3;
        ic1_r = 2.0 * v1 - ic1_r;
        ic2_r = 2.0 * v2 - ic2_r;
        out_r[i] = v2;
    }
    state[0] = ic1_l; state[1] = ic2_l; state[2] = ic1_r; state[3] = ic2_r;
}
