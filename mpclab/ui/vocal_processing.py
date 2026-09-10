"""Vocal processing.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
import threading
from dataclasses import replace
import numpy as np
from .window_client import emit_if_alive
from ..vocal import (
    ProcessingCancelled,
    detect_key,
    note_name,
    render_autotune,
)


def detect_source_key(owner, *, analyze):
    if owner._key_cancel is not None:
        owner._key_cancel.set()
        owner.detect_key_button.setEnabled(False)
        owner.key_progress.setFormat("CANCELLING KEY DETECTION…")
        return
    clip_id = owner.take_box.currentData()
    audio = owner.app.library.audio(clip_id) if clip_id else None
    if audio is None:
        owner.analysis_label.setText("Choose a source take first.")
        return
    settings = replace(owner.app.project.vocal)
    owner._key_source_id = clip_id
    owner._key_project = owner.app.project
    owner._key_job_id += 1
    job_id = owner._key_job_id
    cancel = threading.Event()
    owner._key_cancel = cancel
    owner.detect_key_button.setText("CANCEL KEY DETECTION")
    owner.detect_key_button.setEnabled(True)
    owner.key_progress.show()
    owner.key_progress.setValue(0)
    owner.key_progress.setFormat("ANALYSING KEY… %p%")
    owner.analysis_label.setText("analysing vocal notes…")

    sample_rate = owner.app.engine.sr

    def worker():
        try:
            analysis = analyze(
                audio,
                settings,
                sample_rate,
                progress=lambda value: emit_if_alive(
                    owner, "keyProgress", job_id, int(value * 100)
                ),
                cancelled=cancel.is_set,
            )
            if cancel.is_set():
                raise ProcessingCancelled("key detection cancelled")
            key, scale, confidence = detect_key(analysis)
            emit_if_alive(owner, "keyFinished", job_id, analysis, key, scale, confidence)
        except Exception as exc:
            emit_if_alive(owner, "keyFailed", job_id, str(exc))

    threading.Thread(target=worker, daemon=True).start()


def render_take(owner):
    if owner._tune_cancel is not None:
        owner._tune_cancel.set()
        owner.render_button.setEnabled(False)
        owner.progress.setFormat("CANCELLING TUNE…")
        return
    clip_id = owner.take_box.currentData()
    audio = owner.app.library.audio(clip_id) if clip_id else None
    if audio is None:
        owner.analysis_label.setText("Choose a source take first.")
        return
    source_name = owner.app.library.clips[clip_id].name
    settings = replace(owner.app.project.vocal)
    owner._render_project = owner.app.project
    owner._tune_job_id += 1
    job_id = owner._tune_job_id
    cancel = threading.Event()
    owner._tune_cancel = cancel
    owner._render_source_id = clip_id
    owner.render_button.setText("CANCEL TUNE")
    owner.render_button.setEnabled(True)
    owner.progress.show()
    owner.progress.setValue(1)
    owner.progress.setFormat("ANALYSING + TUNING… %p%")

    sample_rate = owner.app.engine.sr

    def worker():
        try:
            rendered, analysis = render_autotune(
                audio,
                settings,
                sample_rate,
                lambda value: emit_if_alive(owner, "tuneProgress", job_id, int(value * 100)),
                cancel.is_set,
            )
            emit_if_alive(
                owner,
                "tuneFinished",
                job_id,
                rendered,
                analysis,
                f"{source_name} · {settings.key} {settings.scale} tuned",
            )
        except Exception as exc:
            emit_if_alive(owner, "tuneFailed", job_id, str(exc))

    threading.Thread(target=worker, daemon=True).start()


def _key_progress(owner, job_id: int, value: int):
    if job_id == owner._key_job_id and owner._key_cancel is not None:
        owner.key_progress.setValue(value)


def _key_finished(owner, job_id: int, analysis, key: str, scale: str, confidence: float):
    if job_id != owner._key_job_id or owner._key_cancel is None:
        return
    if owner._key_cancel.is_set():
        owner._key_failed(job_id, "key detection cancelled")
        return
    if owner._key_project is not None and (
        owner._key_project is not owner.app.project
        or owner._key_source_id != owner.take_box.currentData()
    ):
        owner._key_failed(job_id, "Source changed; run key detection on the selected take.")
        return
    owner._key_cancel = None
    owner.detect_key_button.setText("DETECT KEY")
    owner.detect_key_button.setEnabled(True)
    owner.key_box.setCurrentText(key)
    owner.scale_box.setCurrentText(scale)
    owner.key_progress.setValue(100)
    owner.key_progress.setFormat("KEY DETECTION COMPLETE")
    owner.analysis_label.setText(
        f"suggested {key} {scale} · {float(confidence):.0%} scale fit · "
        f"{analysis.voiced_fraction:.0%} voiced frames"
    )


def _key_failed(owner, job_id: int, message: str):
    if job_id != owner._key_job_id or owner._key_cancel is None:
        return
    cancelled = owner._key_cancel.is_set() or "cancel" in message.lower()
    owner._key_cancel = None
    owner.detect_key_button.setText("DETECT KEY")
    owner.detect_key_button.setEnabled(True)
    owner.key_progress.setValue(0)
    owner.key_progress.setFormat("KEY DETECTION CANCELLED" if cancelled else "KEY DETECTION FAILED")
    owner.analysis_label.setText(
        "key detection cancelled" if cancelled else f"key detection failed · {message}"
    )


def _tune_progress(owner, job_id: int, value: int):
    if job_id == owner._tune_job_id and owner._tune_cancel is not None:
        owner.progress.setValue(value)


def _tune_finished(owner, job_id: int, audio, analysis, name: str):
    if job_id != owner._tune_job_id or owner._tune_cancel is None:
        return
    if owner._tune_cancel.is_set():
        owner._tune_failed(job_id, "vocal tuning cancelled")
        return
    if owner._render_project is not None and owner._render_project is not owner.app.project:
        owner._tune_failed(job_id, "Project changed; render the take again in this project.")
        return
    if owner._render_source_id not in owner.app.library.clips:
        owner._tune_failed(job_id, "The source take is no longer available.")
        return
    owner._tune_cancel = None
    owner.render_button.setText("Render tuned take")
    owner.render_button.setEnabled(True)
    owner.app.snapshot()
    try:
        source_id = owner._render_source_id
        source = owner.app.library.clips.get(source_id)
        parent = source.parent if source and source.kind == "vocal-tuned" else source_id
        clip = owner.app.library.add_audio(audio, name, kind="vocal-tuned", parent=parent)
    except Exception as exc:
        if hasattr(owner.app, "discard_snapshot"):
            owner.app.discard_snapshot()
        owner._tune_failed_after_save(str(exc))
        return
    wanted = (
        clip.id
        if owner.take_box.currentData() == owner._render_source_id
        else owner.take_box.currentData()
    )
    owner._latest_clip = clip.id
    owner._render_source_id = None
    owner._render_project = None
    owner.refresh_takes(select=wanted)
    owner.app._library_changed()
    voiced = analysis.confidence > 0.35
    median = (
        note_name(float(np.nanmedian(analysis.detected_midi[voiced]))) if np.any(voiced) else "—"
    )
    owner.progress.setValue(100)
    owner.progress.setFormat("TUNED TAKE READY")
    owner.analysis_label.setText(
        f"created {clip.name} · original preserved · "
        f"{analysis.voiced_fraction:.0%} voiced · median note {median}"
    )


def _tune_failed_after_save(owner, message: str):
    owner._render_source_id = None
    owner._render_project = None
    owner.render_button.setEnabled(True)
    owner.progress.setValue(0)
    owner.progress.setFormat("FAILED")
    owner.analysis_label.setText(f"vocal processing failed · {message}")


def _tune_failed(owner, job_id: int, message: str):
    if job_id != owner._tune_job_id or owner._tune_cancel is None:
        return
    cancelled = owner._tune_cancel.is_set() or "cancel" in message.lower()
    owner._tune_cancel = None
    owner._render_source_id = None
    owner._render_project = None
    owner.render_button.setText("Render tuned take")
    owner.render_button.setEnabled(True)
    owner.progress.setValue(0)
    owner.progress.setFormat("TUNE CANCELLED" if cancelled else "FAILED")
    owner.analysis_label.setText(
        "vocal tuning cancelled" if cancelled else f"vocal processing failed · {message}"
    )
