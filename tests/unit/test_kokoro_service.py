from __future__ import annotations

import warnings

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
