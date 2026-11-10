"""Provider registry: maps a provider name to a factory. Replace or add providers with
`register()` (e.g. a vendor SDK or an ONVIF/ISAPI implementation) without other changes."""

from __future__ import annotations

from collections.abc import Callable

from camera.config import Settings
from camera.providers.base import ErrorCode, FrameSource, ProviderError
from camera.providers.opencv_sources import RtspSource, UsbSource
from camera.providers.test_source import TestSource
from camera.spec import SourceSpec

Factory = Callable[[SourceSpec, Settings], FrameSource]


def _rtsp(spec: SourceSpec, s: Settings) -> FrameSource:
    if spec.rtsp is None:
        raise ProviderError(ErrorCode.CONFIG_INVALID, "RTSP camera without rtsp settings")
    return RtspSource(spec.rtsp, s.open_timeout_ms, s.read_timeout_ms)


def _usb(spec: SourceSpec, s: Settings) -> FrameSource:
    if spec.usb is None:
        raise ProviderError(ErrorCode.CONFIG_INVALID, "USB camera without device")
    return UsbSource(spec.usb)


def _test(spec: SourceSpec, s: Settings) -> FrameSource:
    from camera.spec import TestSpec

    return TestSource(spec.test or TestSpec(), spec.name or spec.camera_id[:8], s.test_media_dir)


_PROVIDERS: dict[str, Factory] = {"RTSP": _rtsp, "USB": _usb, "TEST": _test}


def register(name: str, factory: Factory) -> None:
    _PROVIDERS[name] = factory


def create(spec: SourceSpec, settings: Settings) -> FrameSource:
    factory = _PROVIDERS.get(spec.provider)
    if factory is None:
        raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"provider {spec.provider} is not available")
    return factory(spec, settings)
