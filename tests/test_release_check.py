from types import SimpleNamespace

import pytest

from mpclab.release_check import _require_callable_command


def test_require_callable_command_accepts_attached_callable():
    registry = {"recording.settings": SimpleNamespace(callback=lambda: None)}

    _require_callable_command(registry, "recording.settings")


@pytest.mark.parametrize(
    ("registry", "command_id"),
    [
        ({}, "recording.settings"),
        ({"recording.settings": SimpleNamespace(callback=None)}, "recording.settings"),
    ],
)
def test_require_callable_command_rejects_missing_or_noncallable(registry, command_id):
    with pytest.raises(RuntimeError, match=f"Production command is not attached: {command_id}"):
        _require_callable_command(registry, command_id)
