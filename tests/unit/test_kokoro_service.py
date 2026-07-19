from __future__ import annotations

import warnings

import numpy as np

from youtube_kanaal.config import Settings
from youtube_kanaal.services.kokoro_service import KOKORO_REPO_ID, KokoroService


def test_kokoro_pipeline_receives_explicit_repo_id() -> None:
    captured_kwargs: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    service = KokoroService(Settings(kokoro_device="auto"))
    service._import_pipeline = lambda: FakePipeline  # type: ignore[method-assign]

    service._get_pipeline()

    assert captured_kwargs["lang_code"] == "a"
    assert captured_kwargs["repo_id"] == KOKORO_REPO_ID
    assert "device" not in captured_kwargs


def test_kokoro_pipeline_preserves_explicit_device() -> None:
    captured_kwargs: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    service = KokoroService(Settings(kokoro_device="cpu"))
    service._import_pipeline = lambda: FakePipeline  # type: ignore[method-assign]

    service._get_pipeline()

    assert captured_kwargs["device"] == "cpu"


def test_kokoro_dependency_warnings_are_suppressed() -> None:
    service = KokoroService(Settings())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with service._suppress_dependency_warnings():
            warnings.warn(
                "dropout option adds dropout after all but last recurrent layer, so non-zero dropout expects "
                "num_layers greater than 1",
                UserWarning,
            )
            warnings.warn(
                "`torch.nn.utils.weight_norm` is deprecated in favor of "
                "`torch.nn.utils.parametrizations.weight_norm`.",
                FutureWarning,
            )

    assert caught == []


def test_kokoro_uses_one_small_speed_variation_per_short(tmp_path, monkeypatch) -> None:
    service = KokoroService(Settings(kokoro_speed=1.0))
    captured_speeds: list[float] = []
    monkeypatch.setattr("youtube_kanaal.services.kokoro_service.random.uniform", lambda _low, _high: 1.01)
    monkeypatch.setattr(
        service,
        "_generate_audio",
        lambda _text, speed=None: captured_speeds.append(float(speed)) or [np.zeros(32, dtype=np.float32)],
    )

    service.synthesize_beats(
        beats=[
            {"beat_type": "hook", "narration": "A short concrete hook."},
            {"beat_type": "payoff", "narration": "Then the answer lands."},
        ],
        output_path=tmp_path / "narration.wav",
    )

    assert captured_speeds == [1.0807, 0.97465]
