"""Playlist rendering.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QFont, QFontMetrics, QPolygonF
from ..model import Clip
from .theme import q, TRACK_COLORS, is_light
from .waveform import draw_peaks
from .playlist_geometry import HEAD_W, ROW_H, RULER_H


def paintEvent(owner, ev):
    p = QPainter(owner)
    proj = owner.app.project
    w, h = owner.width(), owner.height()
    p.fillRect(owner.rect(), q("canvas"))

    small = QFont(owner.font())
    small.setPointSizeF(8.0)
    p.setFont(small)
    fm = QFontMetrics(small)

    last_beat = owner.x_to_beat(w) + 4

    # The arrangement loop is drawn directly on the ruler and lanes. Drag
    # across the ruler to redefine it; the toolbar switch controls playback.
    loop_a, loop_b = proj.loop_start, proj.loop_end
    if owner._ruler_drag is not None:
        loop_a, loop_b = sorted(owner._ruler_drag)
    if loop_b > loop_a:
        lx = owner.beat_to_x(loop_a)
        lw = (loop_b - loop_a) * owner.px_per_beat
        p.fillRect(QRectF(lx, 0, lw, h), q("accent", 12 if owner.app.engine.loop_song else 4))
        p.setPen(QPen(q("accent", 150), 1))
        p.drawLine(int(lx), 0, int(lx), h)
        p.drawLine(int(lx + lw), 0, int(lx + lw), h)

    # bar grid
    b = 0
    while b <= last_beat:
        x = owner.beat_to_x(b)
        is_bar = b % 4 == 0
        if is_bar or owner.px_per_beat >= 14:
            p.setPen(QPen(q("fg", 46 if is_bar else 16)))
            p.drawLine(int(x), RULER_H, int(x), h)
        b += 1

    # lanes
    any_row_solo = any(row.solo for row in owner.rows())
    for i, row in enumerate(owner.rows()):
        y = RULER_H + i * ROW_H
        row_disabled = row.mute or (any_row_solo and not row.solo)
        if owner._drop_target and owner._drop_target[0] == i:
            p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("accent", 32))
        if i % 2:
            p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("fg", 8))
        if row_disabled:
            p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("rec", 22))
        p.setPen(QPen(q("fg", 30)))
        p.drawLine(0, int(y + ROW_H), w, int(y + ROW_H))

        # header
        p.fillRect(QRectF(0, y, HEAD_W, ROW_H), q("bg2"))
        track_color = row.color or TRACK_COLORS[i % len(TRACK_COLORS)]
        p.fillRect(QRectF(4, y, HEAD_W - 4, ROW_H), q(track_color, 24))
        p.fillRect(QRectF(0, y, 4, ROW_H), q(track_color))
        p.setPen(q("dim") if row_disabled else q("fg"))
        p.drawText(
            QRectF(10, y + 3, HEAD_W - 82, ROW_H / 2),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(row.name, Qt.ElideRight, HEAD_W - 88),
        )
        p.setPen(q("rec") if row.mute else q("dim2"))
        p.drawText(
            QRectF(10, y + ROW_H / 2, HEAD_W - 62, ROW_H / 2 - 4),
            Qt.AlignLeft | Qt.AlignVCenter,
            "MUTED" if row.mute else f"{len(row.clips)} clips",
        )
        capture = getattr(owner.app, "track_capture", None)
        armed = bool(capture and capture.armed_id == row.id)
        if armed:
            p.setPen(q("rec"))
            p.drawText(
                QRectF(10, y + ROW_H / 2, HEAD_W - 82, ROW_H / 2 - 4),
                Qt.AlignRight | Qt.AlignVCenter,
                "REC" if capture.active else "ARMED",
            )
        for label, active, bx in (
            ("R", armed, HEAD_W - 65),
            ("M", row.mute, HEAD_W - 43),
            ("S", row.solo, HEAD_W - 21),
        ):
            br = QRectF(bx, y + 16, 19, 20)
            p.setBrush(q("rec" if label in ("R", "M") else "accent") if active else q("bg3"))
            p.setPen(QPen(q("line"), 1))
            p.drawRoundedRect(br, 3, 3)
            p.setPen(
                q("on_rec")
                if label in ("R", "M") and active
                else q("on_accent")
                if active
                else q("dim")
            )
            p.drawText(br, Qt.AlignCenter, label)

        for clip in row.clips:
            owner._draw_clip(p, clip, y + 3, ROW_H - 7, row_disabled or clip.mute, track_color)

        if capture and capture.active and capture.target.id == row.id:
            left = owner.beat_to_x(capture.start_beat)
            right = owner.beat_to_x(max(capture.start_beat, owner.app.engine.beat))
            recording = QRectF(left, y + 3, max(4, right - left), ROW_H - 7)
            p.fillRect(recording, q("rec", 42))
            p.setPen(QPen(q("rec"), 1))
            p.drawRect(recording)
            p.save()
            p.setClipRect(recording)
            for beat, peak in capture.peaks:
                x = owner.beat_to_x(beat)
                mid = y + ROW_H * 0.65
                amplitude = peak * ROW_H * 0.25
                p.drawLine(QPointF(x, mid - amplitude), QPointF(x, mid + amplitude))
            p.drawText(
                recording.adjusted(6, 1, -2, -2),
                Qt.AlignTop | Qt.AlignLeft,
                "Recording · " + row.name,
            )
            p.restore()

    if owner._drop_target:
        drop_x = owner.beat_to_x(owner._drop_target[1])
        p.setPen(QPen(q("accent"), 2))
        p.drawLine(int(drop_x), RULER_H, int(drop_x), h)

    # header column separator
    p.setPen(QPen(q("line")))
    p.drawLine(HEAD_W, 0, HEAD_W, h)

    # ruler
    p.fillRect(QRectF(0, 0, w, RULER_H), q("bg2"))
    if loop_b > loop_a:
        lx = owner.beat_to_x(loop_a)
        lw = (loop_b - loop_a) * owner.px_per_beat
        p.fillRect(QRectF(lx, 0, lw, RULER_H), q("accent", 52))
        p.setPen(QPen(q("accent_hi"), 2))
        p.drawLine(int(lx), 4, int(lx + lw), 4)
        p.setBrush(q("accent_hi"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(lx, 2), QPointF(lx + 7, 2), QPointF(lx, 10)]))
        p.drawPolygon(
            QPolygonF([QPointF(lx + lw, 2), QPointF(lx + lw - 7, 2), QPointF(lx + lw, 10)])
        )
    p.setPen(q("dim2"))
    b = 0
    while b <= last_beat:
        if b % 4 == 0:
            x = owner.beat_to_x(b)
            p.drawLine(int(x), RULER_H - 7, int(x), RULER_H)
            if owner.px_per_beat * 4 > 28:
                p.drawText(
                    QRectF(x + 3, 0, 40, RULER_H - 6),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    str(b // 4 + 1),
                )
        b += 4

    if owner._marquee is not None:
        a, b = owner._marquee
        box = QRectF(a, b).normalized()
        p.fillRect(box, q("accent", 30))
        p.setPen(QPen(q("accent_hi"), 1, Qt.DashLine))
        p.drawRect(box)

    # playhead
    beat = owner.app.engine.beat
    x = owner.beat_to_x(beat)
    p.setPen(QPen(q("ok"), 1.5))
    p.drawLine(int(x), 0, int(x), h)
    p.setBrush(q("ok"))
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)]))


def _draw_clip(owner, p: QPainter, clip: Clip, y: float, h: float, muted: bool, color=None):
    proj = owner.app.project
    x = owner.beat_to_x(clip.start_beat)
    w = max(5.0, clip.length_beats * owner.px_per_beat)
    rect = QRectF(x, y, w, h)
    is_pattern = clip.kind == "pattern"

    base = (
        (q(color).lighter(175) if is_light() else q(color).darker(235))
        if color
        else q("clip_pat" if is_pattern else "clip_aud")
    )
    if muted:
        base = q("clip_muted")
    p.setBrush(base)
    selected = clip in owner.selected_clips
    border = (
        q("accent_hi")
        if selected
        else q(color)
        if color
        else q("clip_pat_line" if is_pattern else "clip_aud_line")
    )
    p.setPen(QPen(border, 2 if clip is owner._hover_clip or selected else 1))
    p.drawRoundedRect(rect, 3, 3)
    if selected:
        p.setBrush(q("accent_hi"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(x, y + h / 2 - 8, 5, 16), 2, 2)
        p.drawRoundedRect(QRectF(x + w - 5, y + h / 2 - 8, 5, 16), 2, 2)

    p.save()
    p.setClipRect(rect)

    if is_pattern:
        pat = next((pp for pp in proj.patterns if pp.id == clip.ref), None)
        if pat and pat.steps:
            lanes = sorted(pat.steps.keys())
            lane_h = max(1.5, (h - 13) / len(lanes))
            p.setBrush(q("clip_pat_ink", 215))
            p.setPen(Qt.NoPen)
            pat_beats = pat.length_beats
            reps = int(clip.length_beats / pat_beats) + 1
            step_w = max(1.5, owner.px_per_beat / pat.div - 0.5)
            for li, lane in enumerate(lanes):
                for step in pat.steps[lane]:
                    for r in range(reps):
                        beat = r * pat_beats + step / pat.div
                        if beat >= clip.length_beats:
                            break
                        p.drawRect(
                            QRectF(
                                x + beat * owner.px_per_beat,
                                y + 12 + li * lane_h,
                                step_w,
                                max(1.5, lane_h - 0.8),
                            )
                        )
        if pat and pat.notes:
            low = min(n.pitch for n in pat.notes) - 1
            high = max(n.pitch for n in pat.notes) + 1
            p.setPen(Qt.NoPen)
            p.setBrush(q("clip_pat_ink", 210))
            for repeat in range(int(clip.length_beats / pat.length_beats) + 1):
                for note in pat.notes:
                    beat = repeat * pat.length_beats + note.start
                    if beat >= clip.length_beats:
                        continue
                    duration = min(
                        note.duration, pat.length_beats - note.start, clip.length_beats - beat
                    )
                    if duration <= 0:
                        continue
                    note_y = y + 15 + (high - note.pitch) / (high - low) * (h - 21)
                    p.drawRoundedRect(
                        QRectF(
                            x + beat * owner.px_per_beat,
                            note_y,
                            max(2, duration * owner.px_per_beat - 1),
                            2.5,
                        ),
                        1,
                        1,
                    )
    else:
        peaks = owner.app.library.peaks(clip.ref)
        meta = owner.app.library.clips.get(clip.ref)
        if peaks is not None and meta and meta.duration > 0:
            source_secs = clip.source_length or max(0.0, meta.duration - clip.offset)
            source_secs = min(source_secs, max(0.0, meta.duration - clip.offset))
            source_frac = source_secs / meta.duration
            if clip.reverse:
                peaks = peaks[::-1]
                a = max(0.0, (meta.duration - clip.offset - source_secs) / meta.duration)
            else:
                a = clip.offset / meta.duration
            arranged_secs = clip.length_beats * (60.0 / proj.bpm)
            if clip.loop and source_secs > 0:
                cursor = 0.0
                while cursor < arranged_secs - 1e-9:
                    seconds = min(source_secs, arranged_secs - cursor)
                    rw = seconds / arranged_secs * w
                    rx = x + cursor / arranged_secs * w
                    draw_peaks(
                        p,
                        peaks,
                        QRectF(rx, y + 11, rw, h - 13),
                        max(0.0, a),
                        min(1.0, a + source_frac),
                        q("clip_aud_ink", 215),
                    )
                    if cursor > 0:
                        p.setPen(QPen(q("accent_hi", 145), 1))
                        p.drawLine(int(rx), int(y + 11), int(rx), int(y + h))
                    cursor += source_secs
            else:
                span = min(source_secs, arranged_secs) / meta.duration
                draw_peaks(
                    p,
                    peaks,
                    QRectF(x, y + 11, w, h - 13),
                    max(0.0, a),
                    min(1.0, a + span),
                    q("clip_aud_ink", 215),
                )

    p.fillRect(QRectF(x, y, w, 11), q("canvas", 150))
    name = (
        next((pp.name for pp in proj.patterns if pp.id == clip.ref), "pattern")
        if is_pattern
        else (
            owner.app.library.clips.get(clip.ref).name
            if clip.ref in owner.app.library.clips
            else "audio"
        )
    )
    if clip.kind == "audio":
        name = ("⟳ " if clip.loop else "") + ("↶ " if clip.reverse else "") + name
    elif pat := next((pp for pp in proj.patterns if pp.id == clip.ref), None):
        repeats = clip.length_beats / max(0.25, pat.length_beats)
        if repeats > 1.01:
            name += f"  ×{repeats:g}"
    if clip.mute:
        name = "MUTED · " + name
    p.setPen(q("clip_title_ink"))
    p.drawText(QRectF(x + 3, y, w - 6, 11), Qt.AlignLeft | Qt.AlignVCenter, name)
    p.restore()
