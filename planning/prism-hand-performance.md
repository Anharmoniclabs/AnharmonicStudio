# Prism hand performance

## Design hypothesis

A pinch can act like grabbing a physical knob: engage without changing the sound,
move your hand to adjust it, then release without needing a mouse. Recording those
movements as control curves lets a performer shape a new take or overdub an existing
one, edit the result, and export it with the camera turned off.

This implementation adds **Hand control** to Prism's embedded editor in Anharmonic
Studio. The existing VST parameters carry the sound changes. The standalone VST
editor in another DAW does not yet include the camera panel.

## Use it

1. Open **Instruments → Prism → Hand control**.
2. Choose the laptop camera and click **Start camera**.
3. Show one hand. Pinch thumb and index together for a moment to engage.
4. Move while holding the pinch. Release to disengage.

| Gesture | Default destination |
| --- | --- |
| Hand left / right | Tone |
| Hand up / down | Space |
| Spread middle-to-little fingertips | Texture |

The three selectors can also choose layer blend, motion, cutoff, resonance, delay
mix, and reverb mix. Choose three different destinations. Movement sensitivity
controls how far a gesture moves a parameter. Values pick up from the current
sound; a newly detected pinch does not snap a parameter to an absolute position.
The preview is mirrored, with fingertip markers and a hand skeleton.

### Record or overdub movements

Enable **Write gestures during Song playback**, then play the arrangement in
**Song** mode. You can record audio/MIDI at the same time using Studio's normal
record controls, or perform a gesture pass over a take you already recorded.
This checkbox records control curves; it does not start audio recording.

Release the pinch to finish each touched section. Existing points outside that
section are retained, with a return to the previous curve when one exists. A new
curve holds its last value. Looping starts another write pass. Movement while
stopped or in Pattern mode still controls Prism, but does not write arrangement
curves. The panel reports whether it is armed or actually writing.

**Edit recorded curves** opens the existing Automation editor on a Prism lane.
Move/add/delete points, choose interpolation, or bypass the lane. Save the project
to retain curves. Song playback and song export apply enabled Prism curves with
the camera off. While a pinch is engaged, its mapped controls temporarily override
playback curves.

## Camera setup on a source installation

```sh
uv sync --extra camera
uv run python scripts/setup_prism_camera.py
```

The second command downloads a versioned hand model and verifies its SHA-256.
Camera dependencies are optional. Neither installation nor opening the panel opens
a camera. **Start camera** is the explicit switch; **Stop camera** or closing the
panel releases it. Closing Studio also stops the camera before saving recovery.
No camera images or videos are written into a project or sent to a server.

Inference runs in its own process at up to 24 frames/second. The audio callback
never reads camera frames or runs an ML model. Tracking loss, stale frames,
disconnection and invalid landmarks disengage control. Pinch hysteresis, a short
engagement delay, and smoothing reduce accidental changes and jitter.

## Validation and limits

- Automated checks cover pinch pickup, smoothing, tracking loss, invalid inputs,
  parameter bounds, touched-interval recording, persistence, disabled lanes, and
  the live/offline parameter paths.
- A real Prism render checks that automation changes the generated sound; a saved
  song export checks that the curve still works without camera input.
- The HP laptop camera successfully delivered five frames through the actual local
  tracker and was released. No hand was visible during that connection test;
  physical gesture comfort and accuracy still need a performer check.
- This version tracks one hand and writes arrangement automation in Song mode.
  Lighting, occlusion and camera placement affect landmark detection.

The tracker uses Google's [MediaPipe Hand Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python).
Model source: [versioned hand model](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task).
