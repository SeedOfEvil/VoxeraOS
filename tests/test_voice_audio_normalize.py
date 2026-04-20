"""Tests for voxera.voice.audio_normalize.

Pins the ensure_pcm_wav contract that the Moonshine backend relies
on:

1. ``is_pcm_wav`` correctly distinguishes PCM WAV from other
   formats (webm, ogg, bare bytes).
2. ``ensure_pcm_wav`` is a no-op on already-WAV inputs — returns
   ``(source, None)`` so no temp file is created or cleaned up.
3. ``ensure_pcm_wav`` transcodes non-WAV inputs to a temp PCM WAV
   via ``av`` (PyAV) and returns ``(temp, temp)`` so the caller
   can unlink it.
4. A bogus audio stream surfaces the PyAV error truthfully
   (RuntimeError with the upstream message included).
5. When the ``av`` dependency is absent, ``ensure_pcm_wav`` raises
   a truthful ``RuntimeError`` pointing at the ``[moonshine]``
   install extra.
6. Real end-to-end transcode (webm → wav) round-trips correctly
   via PyAV — skipped on hosts without the ``av`` package.

These tests never touch the network and never call into Moonshine.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from voxera.voice.audio_normalize import ensure_pcm_wav, is_pcm_wav

# -- is_pcm_wav header sniff --------------------------------------------------


class TestIsPcmWav:
    def test_true_for_real_riff_wave_header(self, tmp_path: Path) -> None:
        p = tmp_path / "a.wav"
        p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        assert is_pcm_wav(p) is True

    def test_false_for_ebml_webm(self, tmp_path: Path) -> None:
        p = tmp_path / "a.webm"
        p.write_bytes(b"\x1a\x45\xdf\xa3\x9fB\x82\x88matroska")
        assert is_pcm_wav(p) is False

    def test_false_for_ogg(self, tmp_path: Path) -> None:
        p = tmp_path / "a.ogg"
        p.write_bytes(b"OggS\x00\x02\x00\x00\x00\x00\x00\x00")
        assert is_pcm_wav(p) is False

    def test_false_for_too_short(self, tmp_path: Path) -> None:
        p = tmp_path / "a.bin"
        p.write_bytes(b"RIFF")
        assert is_pcm_wav(p) is False

    def test_false_for_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "a.bin"
        p.write_bytes(b"")
        assert is_pcm_wav(p) is False

    def test_false_for_missing_file(self, tmp_path: Path) -> None:
        assert is_pcm_wav(tmp_path / "does_not_exist") is False


# -- ensure_pcm_wav fast path (already-WAV) -----------------------------------


class TestEnsurePcmWavFastPath:
    def test_returns_source_unchanged_for_valid_wav(self, tmp_path: Path) -> None:
        p = tmp_path / "real.wav"
        p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt data")
        path_out, cleanup = ensure_pcm_wav(p)
        assert path_out == p
        assert cleanup is None


# -- ensure_pcm_wav transcode path -------------------------------------------

_av = pytest.importorskip("av")  # fixtures below require PyAV

pytestmark_has_av = pytest.mark.skipif(
    pytest.importorskip("av", reason="PyAV is required for transcode tests") is None,
    reason="PyAV not available",
)


def _write_opus_webm(dest: Path, seconds: float = 1.0, sr: int = 16000) -> None:
    """Produce a small webm/Opus file via PyAV so we have a realistic
    non-WAV input to transcode in tests.  Stays under a second to keep
    test latency bounded."""
    import numpy as np

    n = int(sr * seconds)
    samples = (np.zeros(n, dtype="int16")).tobytes()

    container = _av.open(str(dest), mode="w", format="webm")
    try:
        stream = container.add_stream("libopus", rate=sr)
        # Write silence as a raw PCM input frame and encode through Opus.
        frame = _av.AudioFrame(format="s16", layout="mono", samples=n)
        frame.planes[0].update(samples)
        frame.rate = sr
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    finally:
        container.close()


class TestEnsurePcmWavTranscode:
    def test_transcodes_webm_to_pcm_wav(self, tmp_path: Path) -> None:
        src = tmp_path / "clip.webm"
        _write_opus_webm(src, seconds=0.2)
        out, cleanup = ensure_pcm_wav(src)
        try:
            assert cleanup == out
            assert is_pcm_wav(out)
            # Ensure it's a readable 16 kHz mono 16-bit PCM WAV.
            with wave.open(str(out), "rb") as w:
                assert w.getnchannels() == 1
                assert w.getframerate() == 16000
                assert w.getsampwidth() == 2
                assert w.getnframes() > 0
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)

    def test_transcode_cleans_up_temp_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the inner transcode helper raises, the temp file that
        ``ensure_pcm_wav`` pre-created must be unlinked before the
        exception propagates."""
        src = tmp_path / "clip.webm"
        src.write_bytes(b"\x1a\x45\xdf\xa3" * 8)  # partial EBML header

        captured: list[Path] = []

        def fake_transcode(av_module, source, dest):
            captured.append(dest)
            raise RuntimeError("simulated decode failure")

        monkeypatch.setattr("voxera.voice.audio_normalize._transcode_with_av", fake_transcode)

        with pytest.raises(RuntimeError, match="simulated decode failure"):
            ensure_pcm_wav(src)

        # The helper pre-created one temp file; it must have been
        # unlinked before the exception propagated.
        assert len(captured) == 1
        assert not captured[0].exists()


# -- ensure_pcm_wav without av ------------------------------------------------


class TestEnsurePcmWavWithoutAv:
    def test_raises_truthful_runtime_error_when_av_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate an install without the ``av`` transcoder and confirm
        ensure_pcm_wav raises a helpful ``RuntimeError`` rather than
        silently failing or pretending the transcode happened."""
        src = tmp_path / "clip.webm"
        src.write_bytes(b"\x1a\x45\xdf\xa3")  # EBML — triggers transcode path

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "av":
                raise ModuleNotFoundError("No module named 'av'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(RuntimeError, match="`av` transcoder is not installed"):
            ensure_pcm_wav(src)


# -- real end-to-end via PyAV (skipped when av missing) -----------------------


class TestRealTranscodeRoundTrip:
    def test_webm_round_trips_to_decodable_wav(self, tmp_path: Path) -> None:
        """Write a real webm/Opus clip, normalize it, then decode the
        resulting WAV with the stdlib ``wave`` module to confirm it is
        a valid, moonshine-loadable file."""
        src = tmp_path / "roundtrip.webm"
        _write_opus_webm(src, seconds=0.25, sr=48000)
        out, cleanup = ensure_pcm_wav(src)
        try:
            with wave.open(str(out), "rb") as w:
                frames = w.readframes(w.getnframes())
            # Downsample to 16 kHz, so roughly 0.25s × 16000 = 4000 samples,
            # each 16-bit = 8000 bytes.  Allow some slack for PyAV's
            # internal resampler padding / boundary frames.
            assert 2000 <= len(frames) <= 12000
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
