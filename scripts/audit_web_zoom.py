#!/usr/bin/env python3
"""Headless regressions for waveform navigation and transport shortcuts."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from audit_web_studio import fixture_wav
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(args.root / "website"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks, errors = {}, []

    def check(name, value):
        checks[name] = bool(value)
        assert value, name

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, **({"executable_path": args.browser} if args.browser else {})
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(f"http://127.0.0.1:{server.server_port}/app/studio.html")
            page.wait_for_selector("#pad-grid .pad")
            page.evaluate("""() => {
                const notify = AnharmonicProject.ProjectStore.prototype.notify;
                AnharmonicProject.ProjectStore.prototype.notify = function(...args) { window.zoomStore=this; return notify.apply(this,args); };
                const resume = AnharmonicAudio.AudioEngine.prototype.resume;
                AnharmonicAudio.AudioEngine.prototype.resume = function(...args) { window.zoomEngine=this; return resume.apply(this,args); };
            }""")
            page.locator("#audio-file").set_input_files(
                {"name": "Zoom sine.wav", "mimeType": "audio/wav", "buffer": fixture_wav()}
            )
            page.wait_for_function("document.querySelector('#library-count').textContent === '1'")
            page.locator('[data-workspace="sampler"]').click()

            def view():
                return [
                    float(value.strip())
                    for value in page.locator("#sample-view-range")
                    .inner_text()
                    .removesuffix(" s")
                    .split("–")
                ]

            def wheel(selector, **options):
                fraction = page.locator(selector).evaluate(
                    "(el,o)=>{const r=el.getBoundingClientRect();const e=new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:r.left+r.width*.3,clientY:r.top+r.height*.5,...o});el.dispatchEvent(e);return (e.clientX-r.left)/r.width;}",
                    options,
                )
                page.wait_for_timeout(60)
                return fraction

            original = page.evaluate("zoomStore.toJSON()")
            before = view()
            fraction = wheel("#waveform", deltaY=-200, ctrlKey=True)
            after = view()
            check(
                "pinch_zooms_time_at_cursor",
                after[1] - after[0] < before[1] - before[0]
                and abs(
                    before[0]
                    + fraction * (before[1] - before[0])
                    - after[0]
                    - fraction * (after[1] - after[0])
                )
                < 0.000003,
            )
            image = page.locator("#waveform").evaluate("el=>el.toDataURL()")
            wheel("#waveform", deltaY=-200, altKey=True)
            check(
                "amplitude_zoom_changes_pixels_not_time",
                view() == after
                and page.locator("#waveform").evaluate("el=>el.toDataURL()") != image
                and float(page.locator("#sample-amplitude").input_value()) > 1,
            )
            page.locator("#sample-height").evaluate(
                "el=>{el.value=400;el.dispatchEvent(new Event('input',{bubbles:true}));}"
            )
            check(
                "waveform_height_control_resizes_canvas",
                page.locator(".sampler-canvas").bounding_box()["height"] == 400,
            )
            wheel("#waveform", deltaX=120, deltaY=0)
            check("horizontal_trackpad_pan_changes_visible_start", view()[0] > after[0])
            check(
                "view_navigation_preserves_audio_and_trim",
                page.evaluate("zoomStore.toJSON()") == original,
            )
            page.locator(".sample-fit").click()
            check(
                "reset_restores_both_dimensions",
                view() == before
                and page.locator("#sample-amplitude").input_value() == "1"
                and page.locator(".sampler-canvas").bounding_box()["height"] == 220,
            )
            page.evaluate("zoomStore.setPad(1,{mode:'loop',reverse:true,sync_beats:4})")
            page.locator(".chop-tools").click()
            page.get_by_role(
                "menuitem", name="4 equal slices from the selection", exact=True
            ).click()
            check(
                "chops_clear_stale_loop_and_sync",
                page.evaluate(
                    "zoomStore.project.pads.slice(0,4).every(p=>p.mode==='one-shot'&&!p.reverse&&p.sync_beats===0)"
                ),
            )
            page.locator('[data-workspace="song"]').click()
            page.locator(".tool-draw").click()
            page.locator(".track-lane").first.click(position={"x": 20, "y": 20})
            page.evaluate(
                """zoomStore.transact('waveform fixture', project => project.rows[0].clips.push({id:'wave-check',kind:'audio',ref:project.media[0].id,start_beat:0,length_beats:project.media[0].duration*project.bpm/60,source_length:project.media[0].duration,offset:0,gain:1,track:0,loop:false,reverse:false,mute:false}))"""
            )
            page.locator('[data-workspace="sampler"]').click()
            page.locator('[data-workspace="song"]').click()
            page.wait_for_function(
                """() => {const el=document.querySelector('.clip-waveform');if(!el)return false;return el.getContext('2d').getImageData(0,0,el.width,el.height).data.some((v,i)=>i%4===3 && v>0);}"""
            )
            check(
                "audio_clip_renders_real_visible_waveform",
                page.locator(".clip-waveform").evaluate(
                    "el => {const pixels=el.getContext('2d').getImageData(0,0,el.width,el.height).data;return el.width<=4096 && pixels.some((v,i)=>i%4===3 && v>0 && Math.floor(i/4/el.width)<el.height*.4);}"
                ),
            )
            song = page.evaluate("zoomStore.toJSON()")

            def song_anchor():
                return page.locator(".timeline").evaluate(
                    "el=>{const r=el.getBoundingClientRect(),lane=el.querySelector('.track-lane').getBoundingClientRect();return (r.left+r.width*.3-lane.left)/parseFloat(el.style.getPropertyValue('--song-beat'));}"
                )

            anchor = song_anchor()
            wheel(".timeline", deltaY=-200, ctrlKey=True)
            check(
                "song_pinch_keeps_beat_under_cursor",
                float(page.locator("#song-time-zoom").input_value()) > 100
                and abs(song_anchor() - anchor) < 0.03,
            )
            wheel(".timeline", deltaY=-100, altKey=True)
            check(
                "song_alt_wheel_resizes_track_height",
                page.locator(".track-row").first.bounding_box()["height"] > 58,
            )
            check(
                "song_view_changes_preserve_arrangement",
                page.evaluate("zoomStore.toJSON()") == song,
            )
            check(
                "plain_song_scroll_not_prevented",
                page.locator(".timeline").evaluate(
                    "el=>{const e=new WheelEvent('wheel',{deltaY:40,bubbles:true,cancelable:true});el.dispatchEvent(e);return !e.defaultPrevented;}"
                ),
            )
            page.locator(".song-view-reset").click()
            check(
                "song_reset_restores_view",
                page.locator("#song-time-zoom").input_value() == "100"
                and page.locator(".track-row").first.bounding_box()["height"] == 58,
            )
            page.locator("#play").click()
            page.locator("#stop").click()
            page.evaluate(
                """() => {window.zoomStarts=[];const start=zoomEngine.start;zoomEngine.start=async function(...args){await start.apply(this,args);zoomStarts.push({mode:this.mode,beat:this.anchorBeat});};}"""
            )
            for mode in ["pattern", "song"]:
                page.locator("#playback-mode").select_option(mode)
                page.evaluate(
                    "zoomEngine.stop();zoomEngine.mode=document.querySelector('#playback-mode').value;zoomEngine.paused=true;zoomEngine.pausedBeat=1;document.activeElement.blur();"
                )
                page.wait_for_timeout(400)
                page.keyboard.press("Space")
                page.wait_for_timeout(80)
                page.keyboard.press("Space")
                page.wait_for_timeout(100)
                check(
                    mode + "_double_space_restarts_at_zero",
                    page.evaluate("zoomStarts.at(-1).beat===0 && zoomEngine.playing"),
                )
                page.wait_for_timeout(400)
                page.keyboard.press("Space")
                page.wait_for_function("!zoomEngine.playing")
                page.keyboard.press("Space")
                page.wait_for_function("zoomEngine.playing && zoomStarts.at(-1).beat===0")
                check(mode + "_playing_double_space_restarts_at_zero", True)
                page.wait_for_timeout(400)
                page.keyboard.press("Space")
                page.wait_for_function("zoomEngine.paused")
                paused = page.evaluate("zoomEngine.pausedBeat")
                page.wait_for_timeout(400)
                page.keyboard.press("Space")
                page.wait_for_function("zoomEngine.playing")
                check(
                    mode + "_slow_space_resumes_position",
                    page.evaluate("zoomStarts.at(-1).beat") >= paused > 0,
                )
                page.locator("#stop").click()
            count = page.evaluate("zoomStarts.length")
            page.locator(".project-name").focus()
            page.keyboard.press("Space")
            page.keyboard.press("Space")
            page.wait_for_timeout(100)
            check("double_space_typing_guard", page.evaluate("zoomStarts.length") == count)
            page.screenshot(path=str(args.output / "zoom-song.png"))
            check("no_browser_errors", not errors)
            browser.close()  # Only the isolated headless browser created above.
    finally:
        server.shutdown()
        server.server_close()
        (args.output / "report.json").write_text(
            json.dumps({"checks": checks, "errors": errors}, indent=2) + "\n"
        )
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
