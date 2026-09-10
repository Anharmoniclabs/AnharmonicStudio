#!/usr/bin/env python3
"""Exercise standalone DAW workflows in an isolated headless browser.

Only the browser process created by this script is controlled. Audio input is
synthetic; no real microphone permission is requested. Output is test evidence,
not a claim of exact desktop DSP parity.
"""

import argparse
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import struct
import threading
import traceback
import wave


def fixture_wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(
            b"".join(
                struct.pack("<h", round(12000 * math.sin(index * math.tau * 220 / 44100)))
                for index in range(22050)
            )
        )
    return stream.getvalue()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent / "website"
    args.output.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"workspaces": {}, "browser_errors": [], "checks": {}}
    test_audio = fixture_wav()
    url = f"http://127.0.0.1:{server.server_port}/app/"

    def check(name, condition):
        report["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                **({"executable_path": str(args.browser)} if args.browser else {}),
            )
            try:
                context = browser.new_context(viewport={"width": 1600, "height": 1000})
                page = context.new_page()
                page.on("pageerror", lambda error: report["browser_errors"].append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(url)
                page.wait_for_selector("#pad-grid .pad")
                check("canonical_entry_opens_studio", page.url.endswith("/app/studio.html"))
                check("honest_empty_library", page.locator("#library-count").inner_text() == "0")
                check(
                    "empty_waveform_and_meter",
                    page.locator("#master-meter").evaluate("element => element.value") == 0,
                )
                page.screenshot(path=str(args.output / "web-studio.png"), full_page=True)

                # Record actual model mutations and pad calls without exposing state in production.
                page.evaluate("""() => {
                    window.auditDocument = null; window.auditPads = [];
                    const original = AnharmonicProject.ProjectStore.prototype.notify;
                    AnharmonicProject.ProjectStore.prototype.notify = function(...args) {
                        window.auditDocument = this.toJSON(); return original.apply(this,args);
                    };
                    const trigger = AnharmonicAudio.AudioEngine.prototype.triggerPad;
                    AnharmonicAudio.AudioEngine.prototype.triggerPad = function(index,...args) {
                        window.auditPads.push(index); return trigger.call(this,index,...args);
                    };
                    window.auditPeak = 0;
                    const meter = AnharmonicAudio.AudioEngine.prototype.meter;
                    AnharmonicAudio.AudioEngine.prototype.meter = function(...args) {
                        const result = meter.apply(this,args); window.auditPeak = Math.max(window.auditPeak,result.peak); return result;
                    };
                }""")

                def model():
                    return page.evaluate("window.auditDocument")

                def move_range(selector, value):
                    page.locator(selector).evaluate(
                        "(element,value) => {element.value=value; element.dispatchEvent(new Event('input',{bubbles:true}));}",
                        str(value),
                    )

                def workspace(name):
                    page.locator(f'[data-workspace="{name}"]').click()
                    report["workspaces"][name] = page.locator("#stage button").all_text_contents()

                page.locator("#audio-file").set_input_files(
                    {"name": "Audit sine.wav", "mimeType": "audio/wav", "buffer": test_audio}
                )
                page.wait_for_function(
                    "document.querySelector('#library-count').textContent === '1'"
                )
                check("audio_import_assigns_media", bool(model()["pads"][0]["sample_id"]))
                page.locator("#sound-tree .sound[data-media-id]").dblclick()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('assigned to pad 01')"
                )
                check(
                    "library_double_click_assigns_sound", model()["pads"][0]["name"] == "Audit sine"
                )
                page.wait_for_function("window.auditPeak > 0", timeout=3000)
                check("library_preview_outputs_real_audio", page.evaluate("window.auditPeak > 0"))
                page.locator(".search").fill("no such audio")
                check("search_filters_library", page.locator("#sound-tree .sound").count() == 0)
                page.locator(".search").fill("sine")
                check("search_finds_import", page.locator("#sound-tree .sound").count() == 1)
                page.locator(".search").fill("")
                page.locator('[data-bank="1"]').click()
                page.locator("#use-sound").click()
                check(
                    "bank_assignment_uses_global_pad",
                    model()["pads"][16]["sample_id"] == model()["pads"][0]["sample_id"],
                )
                page.locator("#assign-track").click()
                page.locator(".app-menu button").nth(3).click()
                check("pad_routing_changes_track", model()["pads"][16]["track"] == 3)
                move_range("#pad-pitch", 7)
                check("pad_pitch_persisted", model()["pads"][16]["pitch"] == 7)
                page.locator(".project-name").fill("Release audit 123")
                page.locator(".project-name").press("1")
                page.wait_for_timeout(120)
                before = page.evaluate("auditPads.length")
                page.locator(".project-name").press("2")
                page.wait_for_timeout(100)
                check("typing_does_not_trigger_pads", page.evaluate("auditPads.length") == before)
                page.locator(".project-name").blur()
                page.keyboard.press("1")
                page.wait_for_function("auditPads.length > 0")
                check("shortcuts_respect_bank", page.evaluate("auditPads.at(-1)") == 16)

                workspace("beats")
                page.locator('[data-pad="16"][data-step="0"]').click()
                check(
                    "step_edit_uses_selected_bank", model()["patterns"][0]["steps"]["16"]["0"] == 1
                )
                page.locator(".bars-up").click()
                check(
                    "pattern_length_changes_grid",
                    page.locator('.step-line button[data-pad="16"]').count() == 32,
                )
                page.locator("#step-division").select_option("8")
                check(
                    "pattern_division_changes_grid",
                    page.locator('.step-line button[data-pad="16"]').count() == 64,
                )
                page.locator(".pattern-menu").click()
                page.get_by_role("menuitem", name="+ New empty pattern", exact=True).click()
                check("pattern_creation_selects_new_pattern", model()["selected_pattern"] == 1)
                page.locator(".pattern-menu").click()
                page.get_by_role("menuitem", name="pattern 1", exact=True).click()
                check("pattern_selection_restored", model()["selected_pattern"] == 0)

                workspace("sampler")
                page.locator("#sample-start").fill("0.1")
                page.locator("#sample-start").press("Tab")
                page.locator("#sample-end").fill("0.3")
                page.locator("#sample-end").press("Tab")
                page.locator(".assign-sample").click()
                check(
                    "trim_saved_on_pad",
                    abs(model()["pads"][16]["start"] - 0.1) < 0.0001
                    and abs(model()["pads"][16]["end"] - 0.3) < 0.0001,
                )
                page.locator(".reverse-sample").click()
                check("reverse_persisted", model()["pads"][16]["reverse"])

                workspace("notes")
                page.locator(".note-action").click()
                page.get_by_role("menuitem", name="Pad 17 · Audit sine", exact=True).click()
                grid = page.locator("#note-grid")
                grid.click(position={"x": 20, "y": 150})
                check(
                    "notes_target_selected_sample", model()["patterns"][0]["notes"][-1]["pad"] == 16
                )
                page.locator(".note-root").click()
                page.get_by_role("menuitem", name="D4", exact=True).click()
                check("sample_root_persisted", model()["pads"][16]["root_note"] == 62)
                page.locator(".note-chord").click()
                page.get_by_role("menuitem", name="D4 Minor", exact=True).click()
                check(
                    "chord_respects_selected_root",
                    sorted(note["pitch"] for note in model()["patterns"][0]["notes"][-3:])
                    == [62, 65, 69],
                )
                page.locator(".note-mono").click()
                check("mono_persisted_for_sample", model()["pads"][16]["mono"])
                page.locator(".note-action").click()
                page.get_by_role("menuitem", name="Synthesizer", exact=True).click()
                grid = page.locator("#note-grid")
                grid.click(position={"x": 90, "y": 200})
                check(
                    "synth_notes_have_null_pad", model()["patterns"][0]["notes"][-1]["pad"] is None
                )

                workspace("song")
                page.locator(".song-tool").click()
                page.get_by_role("menuitem", name="DRAW", exact=True).click()
                page.locator(".track-lane").first.click(position={"x": 15, "y": 24})
                check(
                    "draw_places_active_pattern",
                    model()["rows"][0]["clips"][0]["ref"] == model()["patterns"][0]["id"],
                )
                page.locator(".song-tool").click()
                page.get_by_role("menuitem", name="SELECT", exact=True).click()
                clip_box = page.locator(".clip").first.bounding_box()
                page.mouse.move(clip_box["x"] + clip_box["width"] - 3, clip_box["y"] + 20)
                page.mouse.down()
                page.mouse.move(
                    clip_box["x"] + clip_box["width"] + 357, clip_box["y"] + 20, steps=8
                )
                page.mouse.up()
                check("clip_resize_wired", model()["rows"][0]["clips"][0]["length_beats"] == 16)
                page.locator(".song-tool").click()
                page.get_by_role("menuitem", name="SLICE", exact=True).click()
                page.locator(".clip").first.click(position={"x": 360, "y": 20})
                check("slice_creates_two_clips", len(model()["rows"][0]["clips"]) == 2)
                page.locator("#undo-project").click()
                check("undo_restores_clip", len(model()["rows"][0]["clips"]) == 1)
                page.locator("#redo-project").click()
                check("redo_restores_slice", len(model()["rows"][0]["clips"]) == 2)
                page.locator(".song-tool").click()
                page.get_by_role("menuitem", name="MUTE", exact=True).click()
                page.locator(".clip").first.click()
                check("clip_mute_wired", model()["rows"][0]["clips"][0]["mute"])
                page.locator(".clip").first.click()

                workspace("mix")
                move_range('.track-gain[data-track="3"]', 60)
                move_range('.track-pan-input[data-track="3"]', -25)
                move_range("#tone-effect", 3)
                check(
                    "mixer_saved_with_effects",
                    model()["tracks"][3]["gain"] == 0.6
                    and model()["tracks"][3]["pan"] == -0.25
                    and model()["web_effects"]["tone"] == 3,
                )
                check("compression_defaults_to_bypass", model()["web_effects"]["compression"] == 1)
                workspace("instruments")
                page.locator("#synth-preset").select_option("Copper Pluck")
                page.evaluate("window.auditPeak = 0")
                page.locator(".synth-preview").click()
                page.wait_for_function("window.auditPeak > 0", timeout=3000)
                check("synth_preview_produces_meter_signal", page.evaluate("window.auditPeak > 0"))
                page.locator("#stop").click()

                # No device is opened: permission denial must leave controls and storage sane.
                page.evaluate(
                    """() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Audit permission denied','NotAllowedError'); }; }"""
                )
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('Audit permission denied')"
                )
                check(
                    "record_permission_failure_recovers",
                    not page.locator("#record").is_disabled()
                    and "recording" not in (page.locator("#record").get_attribute("class") or ""),
                )

                # Synthetic MediaRecorder also verifies the target is captured before async capture.
                audio_b64 = base64.b64encode(test_audio).decode("ascii")
                page.evaluate(
                    """data => {
                    const blob = new Blob([Uint8Array.from(atob(data), c => c.charCodeAt(0))], {type:'audio/wav'});
                    window.auditStoppedTracks = 0;
                    navigator.mediaDevices.getUserMedia = async () => ({getTracks: () => [{stop: () => auditStoppedTracks++}]});
                    window.MediaRecorder = class extends EventTarget {
                        constructor(){ super(); this.state = 'inactive'; this.mimeType = 'audio/wav'; }
                        start(){ this.state = 'recording'; }
                        stop(){ this.state = 'inactive'; this.dispatchEvent(new MessageEvent('dataavailable',{data:blob})); this.dispatchEvent(new Event('stop')); }
                    };
                }""",
                    audio_b64,
                )
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#record').classList.contains('recording')"
                )
                page.locator('[data-bank="2"]').click()
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('Microphone take captured')"
                )
                check(
                    "recording_preserves_original_pad_target",
                    model()["pads"][16]["name"].startswith("Recording ")
                    and not model()["pads"][32]["sample_id"],
                )
                check("recording_stops_input_tracks", page.evaluate("auditStoppedTracks") == 1)

                workspace("song")
                page.locator('.row-record[data-row-index="1"]').click()
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#record').classList.contains('recording')"
                )
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('Microphone take captured')"
                )
                check(
                    "recording_places_armed_row_clip",
                    model()["rows"][1]["clips"][-1]["kind"] == "audio",
                )
                check(
                    "recording_source_length_is_seconds",
                    abs(model()["rows"][1]["clips"][-1]["source_length"] - 0.5) < 0.01,
                )
                check("recording_releases_each_stream", page.evaluate("auditStoppedTracks") == 2)

                page.evaluate(
                    "() => {window.auditDecode = AudioContext.prototype.decodeAudioData; AudioContext.prototype.decodeAudioData = async () => {throw new Error('Audit decode failure');};}"
                )
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#record').classList.contains('recording')"
                )
                page.locator("#record").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('Recover take')"
                )
                check(
                    "failed_recording_offers_original_take",
                    page.locator("#recover-recording").is_visible(),
                )
                with page.expect_download() as recovered:
                    page.locator("#recover-recording").click()
                recovery_audio = args.output / "microphone-recovery.wav"
                recovered.value.save_as(recovery_audio)
                check(
                    "recording_recovery_preserves_original_bytes",
                    recovery_audio.read_bytes() == test_audio,
                )
                page.evaluate(
                    "() => {AudioContext.prototype.decodeAudioData = window.auditDecode;}"
                )

                with page.expect_download() as original_audio:
                    page.locator("#download-sound").click()
                original_path = args.output / "original-recording.wav"
                original_audio.value.save_as(original_path)
                check("recorded_audio_downloads_for_desktop", original_path.stat().st_size > 44)
                with page.expect_download() as desktop_export:
                    page.locator("#export-desktop").click()
                desktop_path = args.output / "desktop-project.json"
                desktop_export.value.save_as(desktop_path)
                desktop_project = json.loads(desktop_path.read_text())
                check(
                    "desktop_export_is_raw_project",
                    desktop_project["format_version"] == 5
                    and len(desktop_project["pads"]) == 64
                    and "anharmonic_bundle" not in desktop_project,
                )

                page.locator("#save-project").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent.startsWith('Project and audio saved')"
                )
                check(
                    "save_persists_project",
                    page.evaluate(
                        "JSON.parse(localStorage.getItem('anharmonic-web-studio-v2')).media.length"
                    )
                    == 3,
                )
                with page.expect_download() as exported:
                    page.locator("#export-project").click()
                portable_path = args.output / "portable-project.json"
                exported.value.save_as(portable_path)
                portable = json.loads(portable_path.read_text())
                check(
                    "portable_export_contains_all_audio",
                    portable["anharmonic_bundle"] == 1
                    and len(portable["media"]) == 3
                    and all(item["data"] for item in portable["media"]),
                )
                page.reload()
                page.wait_for_selector("#pad-grid .pad")
                check(
                    "saved_library_survives_reload",
                    page.locator("#library-count").inner_text() == "3",
                )
                page.locator("#play").click()
                page.wait_for_function("document.querySelector('#play').textContent === 'Ⅱ'")
                check(
                    "saved_audio_restores_for_playback", page.locator("#play").inner_text() == "Ⅱ"
                )
                page.locator("#stop").click()

                # A separate context has no IndexedDB or localStorage from the first one.
                fresh = browser.new_context(viewport={"width": 1600, "height": 1000})
                second = fresh.new_page()
                second.on("pageerror", lambda error: report["browser_errors"].append(str(error)))
                second.on("dialog", lambda dialog: dialog.accept())
                second.goto(url)
                second.wait_for_selector("#pad-grid .pad")
                second.locator("#project-file").set_input_files(portable_path)
                second.wait_for_function(
                    "document.querySelector('#status').textContent.startsWith('Project loaded.')"
                )
                check(
                    "portable_import_works_in_clean_browser",
                    second.locator("#library-count").inner_text() == "3",
                )
                second.locator("#playback-mode").select_option("song")
                with second.expect_download() as rendered:
                    second.locator("#export-wav").click()
                wav_path = args.output / "song.wav"
                rendered.value.save_as(wav_path)
                with wave.open(str(wav_path), "rb") as rendered_wav:
                    frames = rendered_wav.readframes(rendered_wav.getnframes())
                    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
                    check(
                        "song_export_is_stereo_pcm_wav",
                        rendered_wav.getnchannels() == 2 and rendered_wav.getsampwidth() == 2,
                    )
                    check("song_export_is_audible", max(abs(sample) for sample in samples) > 100)
                    check(
                        "song_export_respects_arrangement_length",
                        rendered_wav.getnframes() / rendered_wav.getframerate() > 4,
                    )

                # Imported names are text, including in every dynamic editor.
                portable["project"]["pads"][0]["name"] = (
                    '<img src=x onerror="window.auditInjected=1">'
                )
                portable["project"]["tracks"][0]["name"] = (
                    '<img src=x onerror="window.auditInjected=1">'
                )
                portable["project"]["rows"][0]["name"] = (
                    '<img src=x onerror="window.auditInjected=1">'
                )
                portable["project"]["patterns"][0]["name"] = (
                    '<img src=x onerror="window.auditInjected=1">'
                )
                second.locator("#project-file").set_input_files(
                    {
                        "name": "hostile-name.json",
                        "mimeType": "application/json",
                        "buffer": json.dumps(portable).encode(),
                    }
                )
                second.wait_for_function(
                    "document.querySelector('#status').textContent.startsWith('Project loaded.')"
                )
                for name in ("song", "beats", "notes", "sampler", "instruments", "mix"):
                    second.locator(f'[data-workspace="{name}"]').click()
                check(
                    "imported_names_cannot_inject_html",
                    second.evaluate(
                        "window.auditInjected !== 1 && document.querySelector('#stage img') === null"
                    ),
                )
                future_error = second.evaluate(
                    "() => {try {AnharmonicProject.normalize({format_version:999}); return '';} catch(error){return error.message;}}"
                )
                second.locator("#project-file").set_input_files(
                    {
                        "name": "future.json",
                        "mimeType": "application/json",
                        "buffer": b'{"format_version":999}',
                    }
                )
                second.wait_for_function(
                    "message => document.querySelector('#status').textContent === message",
                    arg=future_error,
                )
                check(
                    "invalid_project_keeps_loaded_session",
                    second.locator("#library-count").inner_text() == "3",
                )
                second.locator("#focus-toggle").click()
                check(
                    "focus_control_hides_side_panels",
                    "hide-browser" in second.locator(".main-split").get_attribute("class")
                    and "hide-pads" in second.locator(".main-split").get_attribute("class"),
                )
                second.locator("#focus-toggle").click()
                second.screenshot(path=str(args.output / "web-studio-mix.png"), full_page=True)
                second.set_viewport_size({"width": 390, "height": 844})
                second.screenshot(path=str(args.output / "web-studio-mobile.png"), full_page=True)
                second.locator("#help-toggle").click()
                check("mobile_help_remains_accessible", second.locator("#help-dialog").is_visible())
                second.locator("#help-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                check(
                    "mobile_transport_buttons_keep_touch_width",
                    all(
                        second.locator(selector).bounding_box()["width"] >= 32
                        for selector in ("#play", "#stop", "#record")
                    ),
                )
                fresh.close()

                # Corrupt local storage must be preserved, never silently removed.
                recovery = browser.new_context()
                recovery_page = recovery.new_page()
                recovery_page.goto(url)
                recovery_page.wait_for_selector("#pad-grid .pad")
                recovery_page.evaluate(
                    "localStorage.setItem('anharmonic-web-studio-v2', '{broken user data')"
                )
                recovery_page.reload()
                recovery_page.wait_for_selector("#pad-grid .pad")
                check(
                    "corrupt_save_retained",
                    recovery_page.evaluate("localStorage.getItem('anharmonic-web-studio-v2')")
                    == "{broken user data",
                )
                check(
                    "corrupt_save_has_visible_recovery_message",
                    "retained" in recovery_page.locator("#status").inner_text(),
                )
                recovery.close()
                check("no_browser_exceptions", not report["browser_errors"])
            except Exception:
                report["failure"] = traceback.format_exc()
                if "page" in locals():
                    page.screenshot(path=str(args.output / "failure.png"), full_page=True)
            finally:
                browser.close()  # Only our newly launched headless browser.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["checks"], indent=2))
    if report.get("failure"):
        print(report["failure"])
    return (
        0
        if report["checks"] and all(report["checks"].values()) and not report.get("failure")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
