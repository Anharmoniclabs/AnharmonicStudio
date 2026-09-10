#!/usr/bin/env python3
"""Exercise the web audio engine using real Chromium OfflineAudioContexts.

Run with the audit environment's Python (Playwright must be installed). Only a
new, headless browser is launched and closed; desktop/chat windows are untouched.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument(
        "--output", type=Path, help="Write the full audio regression report as JSON."
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            **({"executable_path": str(args.browser)} if args.browser else {}),
        )
        try:
            page = browser.new_page()
            page.add_script_tag(path=str(root / "website/app/project-model.js"))
            page.add_script_tag(path=str(root / "website/app/audio-engine.js"))
            results = page.evaluate(r"""async () => {
              const results = []; const { AudioEngine, collectEvents, LIMITS } = AnharmonicAudio;
              const assert = (condition, message) => { if (!condition) throw new Error(message); };
              const equal = (actual, expected, epsilon = 1e-4) => assert(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);
              const test = async (name, fn) => { try { await fn(); results.push({ name, passed: true }); } catch (error) { results.push({ name, passed: false, error: error.stack }); } };
              const factory = () => {
                const project = AnharmonicProject.defaultProject(); project.bpm = 120; project.master = 1;
                project.patterns[0].steps = {}; project.patterns[0].notes = [];
                project.tracks.forEach(track => { track.gain = 1; track.pan = 0; track.fx = {}; });
                project.synth = { ...project.synth, noise: 0, sub: 0, lfo_pitch: 0 };
                const context = new OfflineAudioContext(2, 44100, 44100); const buffers = new Map();
                const sample = context.createBuffer(1, 44100, 44100); sample.getChannelData(0).fill(.4); buffers.set('tone', sample);
                project.pads[0] = { ...project.pads[0], sample_id: 'tone', attack: 0, release: 0 };
                const engine = new AudioEngine({ getProject: () => project, getBuffer: id => buffers.get(id) });
                return { project, engine, buffers, context };
              };
              const pcm = async blob => {
                const view = new DataView(await blob.arrayBuffer()); const channels = view.getUint16(22, true); const rate = view.getUint32(24, true);
                return { view, channels, rate, duration: (view.byteLength - 44) / 2 / channels / rate,
                  at: (time, channel = 0) => view.getInt16(44 + (Math.floor(time * rate) * channels + channel) * 2, true) / 32768,
                  peak: (start = 0, end = (view.byteLength - 44) / 2 / channels / rate, channel = 0) => {
                    let peak = 0; for (let frame = Math.floor(start * rate); frame < Math.floor(end * rate); frame += 1) peak = Math.max(peak, Math.abs(view.getInt16(44 + (frame * channels + channel) * 2, true) / 32768)); return peak;
                  }
                };
              };
              await test('arbitrary bars/division/velocity and native off-eighth swing', () => {
                const { project } = factory(); const pattern = project.patterns[0]; pattern.bars = 3; pattern.div = 8; pattern.steps = { 0: { 0: .25, 4: .5, 95: 1 } }; project.swing = 50;
                const events = collectEvents(project, 'pattern', 0, 12);
                assert(events.length === 3, 'three events expected'); equal(events[1].beat, .5 + .5 / 8 * .66); equal(events[1].velocity, .5);
                equal(events[2].beat, 95 / 8 + .5 / 8 * .66);
                const adjacent = [...collectEvents(project, 'pattern', 0, .5), ...collectEvents(project, 'pattern', .5, 12)];
                assert(JSON.stringify(events) === JSON.stringify(adjacent), 'window boundaries duplicated or dropped hits');
              });
              await test('zero velocity or duration notes remain silent', () => {
                const { project } = factory(); project.patterns[0].notes = [{ start: 0, duration: 0, velocity: 1, pitch: 60, pad: null }, { start: 1, duration: 1, velocity: 0, pitch: 60, pad: 0 }];
                assert(collectEvents(project, 'pattern', 0, 4).length === 0, 'silent notes scheduled');
              });
              await test('offline PCM applies velocity, pad/track/master gain and silence', async () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: .5 } }; project.pads[0].gain = .5; project.tracks[0].gain = .5; project.master = .5;
                let output = await pcm(await engine.render('pattern', { tail: 0 }));
                equal(output.at(.1), .4 * .5 ** 4 / Math.sqrt(2)); equal(output.duration, 2);
                project.master = 0; output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(), 0);
              });
              await test('trim, reverse, chromatic pitch and pan use the imported sample', async () => {
                const { project, engine, buffers } = factory(); const data = buffers.get('tone').getChannelData(0);
                for (let i = 0; i < data.length; i += 1) data[i] = i / data.length * .5;
                Object.assign(project.pads[0], { start: .2, end: .6, reverse: true, pan: -1, root_note: 60 });
                project.patterns[0].notes = [{ start: 0, duration: 1, velocity: 1, pitch: 72, pad: 0 }];
                const output = await pcm(await engine.render('pattern', { tail: 0 }));
                equal(output.at(.05), .25, .001); equal(output.peak(0, .25, 1), 0); equal(output.peak(.21, .4), 0);
              });
              await test('gate notes end at their duration and looped pads fill their gate', async () => {
                const { project, engine } = factory(); Object.assign(project.pads[0], { mode: 'gate', end: .1 });
                project.patterns[0].notes = [{ start: 0, duration: .5, velocity: 1, pitch: 60, pad: 0 }];
                let output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(.11, .24), 0);
                project.pads[0].mode = 'loop'; output = await pcm(await engine.render('pattern', { tail: 0 }));
                assert(output.at(.2) > .2, 'loop failed to fill gate'); equal(output.peak(.26, .4), 0);
              });
              await test('track mute and solo suppress pads and synth in the actual render', async () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1 } }; project.tracks[1].solo = true;
                let output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(), 0);
                project.tracks[1].solo = false; project.tracks[0].mute = true; output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(), 0);
              });
              await test('track faders follow inserts and stereo balance does not crossfeed', async () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1 } }; project.tracks[0].fx = { drive: .7 };
                const loud = await pcm(await engine.render('pattern', { tail: 0 })); project.tracks[0].gain = .5;
                const quiet = await pcm(await engine.render('pattern', { tail: 0 })); equal(quiet.at(.1) / loud.at(.1), .5, .001);
                project.tracks[0].fx = {}; project.tracks[0].gain = 1; project.tracks[0].pan = 1;
                const balanced = await pcm(await engine.render('pattern', { tail: 0 })); equal(balanced.at(.1), 0); equal(balanced.at(.1, 1), .4 / Math.sqrt(2));
              });
              await test('song audio trim/loop, pattern repeats, row mute/solo and clip gain', async () => {
                const { project, engine } = factory(); const row = project.rows[0];
                row.clips = [{ id: 'audio', kind: 'audio', ref: 'tone', start_beat: 1, length_beats: 2, offset: .2, source_length: .1, loop: true, gain: .5, track: 0 }];
                let output = await pcm(await engine.render('song', { tail: 0 }));
                equal(output.duration, 1.5); equal(output.peak(0, .49), 0); assert(output.at(1.2) > .1, 'audio loop ended early');
                row.mute = true; output = await pcm(await engine.render('song', { tail: 0 })); equal(output.peak(), 0);
                row.mute = false; project.rows[1].solo = true; output = await pcm(await engine.render('song', { tail: 0 })); equal(output.peak(), 0);
                project.rows[1].solo = false; project.patterns[0].steps = { 0: { 0: .8 } }; row.clips = [{ kind: 'pattern', ref: project.patterns[0].id, start_beat: 2, length_beats: 6, gain: .5 }];
                const events = collectEvents(project, 'song', 0, 8); assert(events.length === 2, 'pattern clip did not repeat'); equal(events[0].beat, 2); equal(events[1].beat, 6); equal(events[1].gain, .5);
              });
              await test('choke groups stop previously scheduled voices at the choke event', async () => {
                const { project, engine } = factory(); project.pads[0].release = .03; project.pads[1] = { ...project.pads[0], pan: 1, choke: 1 }; project.pads[0].pan = -1; project.pads[0].choke = 1;
                project.patterns[0].steps = { 0: { 0: 1 }, 1: { 1: 1 } };
                const output = await pcm(await engine.render('pattern', { tail: 0 })); assert(output.at(.05) > .3, `first pad missing (${output.at(.05)}, peak ${output.peak(0, .1)})`); equal(output.at(.2), 0); assert(output.at(.2, 1) > .3, `choking pad missing (${output.at(.2, 1)})`);
              });
              await test('overlapping pattern placements retain separate choke ownership', async () => {
                const { project, engine } = factory(); project.pads[0].pan = -1; project.pads[0].choke = 1; project.pads[1] = { ...project.pads[0], pan: 1 };
                project.patterns[0].steps = { 0: { 0: 1 } }; project.patterns.push({ ...project.patterns[0], id: 'second', steps: { 1: { 1: 1 } } });
                project.rows[0].clips = [{ id: 'first-placement', kind: 'pattern', ref: project.patterns[0].id, start_beat: 0, length_beats: 2 }];
                project.rows[1].clips = [{ id: 'second-placement', kind: 'pattern', ref: 'second', start_beat: 0, length_beats: 2 }];
                const output = await pcm(await engine.render('song', { tail: 0 })); assert(output.at(.2) > .3 && output.at(.2, 1) > .3, 'overlapping placements stole each other’s voices');
              });
              await test('loop crossfade preserves first head and resumes after consumed head', async () => {
                const { project, engine, buffers, context } = factory(); const buffer = buffers.get('tone'); const data = buffer.getChannelData(0);
                for (let i = 0; i < data.length; i += 1) data[i] = i / buffer.sampleRate;
                const graph = { trackBuses: [{ input: context.destination }] }; const pool = new Set();
                engine.bufferVoice(buffer, { start: 0, end: .1, mode: 'loop', loop_crossfade: .01, pan: -1, gain: 1, attack: 0, release: 0, track: 0 }, { when: 0, duration: .5, project }, context, graph, pool, true);
                const output = await pcm(AnharmonicAudio.encodeWav(await context.startRendering())); equal(output.at(.02), .02); equal(output.at(.12), .03, .001); equal(output.at(.21), .03, .001);
              });
              await test('oscillator notes export audible finite PCM and release', async () => {
                const { project, engine } = factory(); project.patterns[0].notes = [{ start: .5, duration: .25, velocity: .8, pitch: 60, pad: null }]; project.synth.release = .1;
                const output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(0, .24), 0); assert(output.peak(.26, .4) > .01, 'synth inaudible'); equal(output.peak(.6, 1), 0);
              });
              await test('pulse and noise oscillator selections render their actual waveforms', async () => {
                const { project, engine } = factory(); project.patterns[0].notes = [{ start: 0, duration: .5, velocity: .8, pitch: 60, pad: null }];
                project.synth.osc1 = 'pulse'; project.synth.osc2 = 'noise'; project.synth.pulse_width = .2;
                const output = await pcm(await engine.render('pattern', { tail: 0 })); assert(output.peak(.02, .2) > .01, 'pulse/noise patch inaudible');
              });
              await test('master effects are persisted into offline WAV', async () => {
                const { project, engine } = factory(); project.pads[0].end = .05; project.patterns[0].steps = { 0: { 0: 1 } }; project.web_effects = { delay: 40 };
                const output = await pcm(await engine.render('pattern', { tail: 0 })); assert(output.peak(.25, .31) > .03, 'saved delay missing from export');
              });
              await test('missing referenced media fails export explicitly; empty pads are silent', async () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1 } }; project.pads[0].sample_id = 'missing-file';
                let message = ''; try { await engine.render(); } catch (error) { message = error.message; }
                assert(message.includes('missing-file') && message.includes('Missing audio'), 'missing media was not reported');
                project.pads[0].sample_id = ''; const output = await pcm(await engine.render('pattern', { tail: 0 })); equal(output.peak(), 0);
              });
              await test('exports reject excessive duration and simultaneous voices', async () => {
                const { project, engine } = factory(); project.patterns[0].bars = 256; let message = '';
                try { await engine.render(); } catch (error) { message = error.message; } assert(message.includes('limited'), 'oversized export not rejected');
                project.patterns[0].bars = 1; project.pads[1] = { ...project.pads[0] }; project.patterns[0].notes = Array.from({ length: LIMITS.voices + 1 }, (_, index) => ({ start: 0, duration: 1, velocity: 1, pitch: index % 128, pad: Math.floor(index / 128) })); message = '';
                try { await engine.render(); } catch (error) { message = error.message; } assert(message.includes('simultaneous voices'), 'polyphony not bounded');
              });
              await test('unsupported active native processors fail explicitly and bypassed state survives', async () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1 } };
                const fixtures = [
                  ['automation', [{ target: 'master', points: [{ beat: 0, value: .2 }], enabled: true }]],
                  ['plugins', { effect: { path: '/private/plugin.vst3', bypass: false } }],
                  ['pro_daw', { plugin_chains: { master: [{ path: '/private/plugin.vst3', bypass: false }] } }],
                  ['workflow', { routing: { buses: [{ id: 'bus' }] } }]
                ];
                for (const [key, value] of fixtures) {
                  project[key] = value; let message = ''; try { await engine.render(); } catch (error) { message = error.message; }
                  assert(message.includes('cannot reproduce'), `unsupported ${key} silently rendered`); delete project[key];
                }
                project.plugins = { effect: { path: '/private/plugin.vst3', bypass: true } }; const before = JSON.stringify(project.plugins);
                const output = await pcm(await engine.render('pattern', { tail: 0 })); assert(output.peak() > .1, 'bypassed plugin prevented supported playback'); assert(JSON.stringify(project.plugins) === before, 'preserved plugin state was changed');
              });
              await test('lookahead uses audio clock, changes tempo without resetting beat, cancels stop', () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1, 1: 1, 2: 1 } };
                engine.context = { currentTime: 0 }; engine.graph = { sync() {} }; engine.playing = true; engine.mode = 'pattern'; engine.tempo = 120; engine.project = project; engine.anchorTime = .04; engine.anchorBeat = 0; engine.cursor = 0;
                const scheduled = []; engine.schedule = (event, when) => scheduled.push({ beat: event.beat, when });
                engine.tick(); equal(scheduled[0].when, .04); engine.context.currentTime = .1; engine.tick(); equal(scheduled[1].when, .165);
                engine.context.currentTime = .15; project.bpm = 60; engine.sync(); const position = engine.anchorBeat; equal(position, .236); engine.tick(); engine.context.currentTime = .4; engine.tick();
                const next = scheduled.find(event => event.beat === .5); assert(next, 'next tempo event missing'); equal(next.when, .158 + (.5 - position));
                let stopped = 0; engine.voices.add({ when: 99, end: 100, stop() { stopped += 1; } }); engine.graph = null; engine.stop(); assert(!engine.playing && engine.timer === null && !engine.voices.size && stopped === 1, 'stop did not cancel sources');
              });
              await test('song loops schedule the next cycle ahead on the same audio clock', () => {
                const { project, engine } = factory(); project.patterns[0].steps = { 0: { 0: 1 } }; project.rows[0].clips = [{ kind: 'pattern', ref: project.patterns[0].id, start_beat: 0, length_beats: 4, gain: 1 }]; project.loop_enabled = true; project.loop_start = 0; project.loop_end = 4;
                engine.context = { currentTime: 1.95 }; engine.graph = { sync() {} }; engine.playing = true; engine.mode = 'song'; engine.tempo = 120; engine.project = project; engine.anchorTime = .04; engine.anchorBeat = 0; engine.cursor = 3.8;
                const scheduled = []; engine.schedule = (event, when) => { scheduled.push({ beat: event.beat, when }); }; engine.tick();
                assert(scheduled.length === 1, 'next loop event was not scheduled in lookahead'); equal(scheduled[0].when, 2.04); equal(engine.anchorTime, .04);
              });
              await test('lookahead retains still-audible voices so stop can cancel every source', () => {
                const { engine } = factory(); engine.context = { currentTime: 0 };
                let stopped = 0; const first = { when: 0, end: .05, stop() { stopped += 1; } }; const second = { when: .1, end: .2, stop() { stopped += 1; } };
                engine.registerVoice(first); engine.registerVoice(second); assert(engine.voices.size === 2, 'future scheduler pruned an audible source'); engine.stop(); assert(stopped === 2, 'stop omitted an audible source');
              });
              await test('tempo changes retime sustained gates while retaining musical position', () => {
                const { project, engine } = factory(); engine.context = { currentTime: .5 }; engine.graph = { sync() {} }; engine.playing = true; engine.tempo = 120; engine.project = project; engine.anchorTime = 0; engine.anchorBeat = 0;
                let retimed = 0; engine.voices.add({ when: 0, end: 3, endBeat: 4, retime(time) { retimed = time; }, stop() {} }); project.bpm = 60; engine.sync(); equal(retimed, .508 + (4 - 1.016));
              });
              await test('future voice eviction stays bounded and Stop owns pending cancellations', () => {
                const { engine } = factory(); engine.context = { currentTime: 0 }; const all = []; const victims = new Set();
                for (let index = 0; index < 300; index += 1) {
                  const voice = { when: .1, end: 1, stop(time) { this.end = Math.min(this.end, time); if (time === .1) victims.add(index); } };
                  all.push(voice); engine.registerVoice(voice);
                }
                assert(engine.voices.size === LIMITS.voices, 'future voices exceeded the active pool cap'); assert(victims.size === 300 - LIMITS.voices, 'same victim was repeatedly stolen');
                assert(all.filter(voice => voice.end > .1).length === LIMITS.voices, 'too many voices survive at scheduled start');
                engine.stop(); assert(all.every(voice => voice.end === 0), 'pending cancellations escaped Stop'); assert(!engine.retiringVoices.size, 'retired voices retained after Stop');
              });
              await test('offline synth voices share one deterministic noise buffer per context', async () => {
                const { project, engine, context } = factory(); Object.assign(project.synth, { osc1: 'noise', osc2: 'noise', noise: .2, sub: 0, lfo_pitch: 0, release: .01 });
                const createBuffer = context.createBuffer.bind(context); let allocated = 0; context.createBuffer = (...args) => { allocated += 1; return createBuffer(...args); };
                const pool = new Set(); const graph = { trackBuses: Array.from({ length: 8 }, () => ({ input: context.destination })) };
                for (let index = 0; index < 20; index += 1) engine.synthVoice(60, { project, when: index * .02, duration: .01 }, context, graph, pool, true);
                assert(allocated === 1, `noise allocated ${allocated} buffers for one context`);
                const output = await pcm(AnharmonicAudio.encodeWav(await context.startRendering())); assert(output.peak(.02, .4) > .01, 'shared noise voices were silent');
              });
              await test('offline graph resource preflight rejects dense synth allocation before building nodes', async () => {
                const { project, engine } = factory(); project.patterns[0].bars = 128;
                project.patterns[0].notes = Array.from({ length: 2100 }, (_, index) => ({ pitch: 60, start: index * .24, duration: .1, velocity: .8, pad: null }));
                const original = OfflineAudioContext.prototype.createBuffer; let allocated = 0;
                OfflineAudioContext.prototype.createBuffer = function (...args) { allocated += 1; return original.apply(this, args); };
                let message = ''; try { await engine.render('pattern', { tail: 0 }); } catch (error) { message = error.message; } finally { OfflineAudioContext.prototype.createBuffer = original; }
                assert(message.includes('resource budget'), 'dense synth allocation was not bounded'); assert(allocated === 0, 'buffers were allocated before resource preflight');
              });
              return results;
            }""")
        finally:
            browser.close()  # Only the isolated headless browser created above.
    report = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report, end="")
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
