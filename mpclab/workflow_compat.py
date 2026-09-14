"""Compatibility bridge between the legacy shortcut table and command registry."""

from __future__ import annotations

from PySide6.QtGui import QKeySequence


def restore_unmanaged_legacy_shortcuts(controller) -> None:
    """Keep old shortcuts alive unless the command layer explicitly owns them.

    MainWindow may gain another global key before the command catalog does.  A
    new workflow release must never silently disable that key, so unknown legacy
    QShortcuts remain enabled while managed ones are exclusively handled by the
    application-level command filter.
    """
    managed = set(controller._legacy_sequences)
    managed.update(controller._binding_index)
    for shortcut in getattr(controller.window, "_shortcuts", []):
        sequence = shortcut.key().toString(QKeySequence.PortableText).casefold()
        shortcut.setEnabled(sequence not in managed)
