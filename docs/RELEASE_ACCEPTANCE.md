# Release acceptance

A release grade is a judgment, not something a passing test counter certifies.
These checks define what still needs to be demonstrated before calling the app
ready for dependable daily recording. Record the app build, operating system,
audio interface, sample rate, buffer size and outcome for each hardware session.

## Automated checks

- `scripts/quality.sh`: full application/controller tests with a memory guard,
  Python/native DSP checks, static checks and short callback benchmarks.
- `scripts/build_linux_bundle.py`: build, offscreen save/load/export/FFmpeg check,
  disposable user installation and installed-copy check. Does not play audio.
- `scripts/soak_production.py --seconds 600 --frames 512 --max-p99-load 0.75
  --max-late-fraction 0.001 --max-growth-mib 32`: paced mixed instruments/effects,
  75% p99 processing budget, at most 0.1% software processing overruns and at most
  32 MiB observed RSS growth. This tolerance is a regression threshold, not an
  allowance for audible glitches. Wake-up lateness is reported separately.
- `python -m mpclab --self-check`: source build equivalent of the installed check.

## Musician and hardware session

1. Start with the intended interface at 48 kHz and 512 frames. Verify the correct
   input/output, record a short take, play it back and export/reimport it.
2. Work for at least an hour: sample drums, edit notes, loop a song, mix effects,
   record several takes, save, close and reopen. Check timing and playback by ear,
   not only whether the application stays open.
3. While recording, test pause/resume and save retry using a disposable project.
   Confirm the recovered take remains aligned. Do not use a valuable live take
   to deliberately test storage failures.
4. Disconnect/reconnect the intended USB interface. Verify the app reports the
   loss and can resume after selecting an available device, without losing edits.
5. With the interface's documented cable-loopback setup, measure round-trip
   latency and confirm recorded notes align after compensation.
6. Repeat a large project and export on the slowest supported storage. Test
   optional stem separation during playback in the separate source environment.

## Distribution evidence

- Validate a clean installation on every advertised OS/architecture. A passing
  CachyOS build does not prove Ubuntu, Windows or macOS support.
- Verify dependency licenses/notices and corresponding-source delivery, then
  signing and the private official download channel.
- Have another musician complete import → arrangement → recording → export
  without developer assistance and record any confusing or failed steps.

Physical-device and musician acceptance remain pending until performed. Software
soaks do not measure interface latency, missing hardware samples or driver xruns.
