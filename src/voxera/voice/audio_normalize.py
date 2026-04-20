"""Audio-format normalization helper for the Moonshine STT path.

Moonshine (``moonshine-voice``) reads PCM WAV files only via its
bundled pure-Python loader.  Real operator inputs on the Voice
Workbench mic-upload lane arrive as browser-captured ``audio/webm``
(Opus) because the MediaRecorder API on most browsers does not
natively produce PCM WAV.  Rather than force the operator to
convert manually — or fake success when Moonshine would actually
fail — we transcode unsupported audio to 16-bit PCM WAV (mono,
16 kHz) transparently *inside* the backend, so the canonical
``transcribe_audio_file`` seam keeps the same shape.

Design rules:

- **Fast path first**: if the input file already starts with a
  ``RIFF....WAVE`` header, skip transcoding entirely and let
  ``load_wav_file`` read it directly.  Already-normalized WAVs
  pay zero conversion cost.
- **Bounded scope**: the normalizer serves the Moonshine backend
  only.  Whisper's FFmpeg-backed decoder stays untouched.
- **Optional dep**: transcoding uses ``av`` (PyAV) which is pulled
  by the ``[moonshine]`` install extra.  If ``av`` is somehow
  missing at runtime we surface a truthful ``RuntimeError`` so
  the backend reports ``backend_error`` with a clear message,
  never a fake-success.
- **Temp file hygiene**: the normalizer returns a
  ``(source_or_temp_path, cleanup_path_or_none)`` tuple.  When a
  temp WAV was produced the caller is responsible for unlinking
  ``cleanup_path`` after transcription — the Moonshine backend
  does this in a ``finally`` block.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import wave
from pathlib import Path

# -- Moonshine expects these parameters for its non-streaming decode path.
# 16 kHz mono 16-bit PCM is the baseline Moonshine model input rate; the
# bundled ``load_wav_file`` also accepts 24/32-bit PCM but standardising
# the transcoder output on 16-bit PCM keeps the file size small and the
# decode path identical across inputs.
_TARGET_SAMPLE_RATE = 16000
_TARGET_CHANNELS = 1
_TARGET_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM
_TEMP_PREFIX = "voxera_moonshine_norm_"
_TEMP_SUFFIX = ".wav"


def is_pcm_wav(path: Path) -> bool:
    """Return True if *path* begins with a PCM ``RIFF....WAVE`` header.

    This is a cheap header sniff, not a full WAV validation: the
    12-byte ``RIFF``/``WAVE`` prefix is all we need to decide
    "skip transcode" vs "transcode".  ``load_wav_file`` inside
    moonshine-voice is the canonical validator — if this sniff
    passes but the WAV is malformed further in, Moonshine will
    raise its own truthful error.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(12)
    except OSError:
        return False
    return len(header) == 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"


def ensure_pcm_wav(source: Path) -> tuple[Path, Path | None]:
    """Return a (path, cleanup_path) tuple suitable for Moonshine.

    - If *source* is already a PCM WAV, returns ``(source, None)``.
      Caller does nothing.
    - Otherwise, transcodes *source* to a new temp 16-bit PCM WAV
      and returns ``(temp_path, temp_path)``.  The caller MUST
      ``os.unlink(cleanup_path)`` once it is done reading the file
      (typically in a ``finally`` block around the transcription
      call).

    Raises ``RuntimeError`` if the transcoder is unavailable or
    the transcode itself fails — the Moonshine backend wraps this
    as ``backend_error`` so the operator sees a truthful failure.
    """
    if is_pcm_wav(source):
        return source, None

    try:
        import av
    except ModuleNotFoundError as exc:  # pragma: no cover — covered by test via monkeypatch
        raise RuntimeError(
            "audio is not PCM WAV and the `av` transcoder is not installed. "
            "Reinstall with `pip install voxera-os[moonshine]` or provide "
            "a PCM WAV file."
        ) from exc

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=_TEMP_SUFFIX, prefix=_TEMP_PREFIX)
    os.close(tmp_fd)
    tmp = Path(tmp_path)
    try:
        _transcode_with_av(av, source, tmp)
        return tmp, tmp
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _transcode_with_av(av_module, source: Path, dest: Path) -> None:
    """Decode *source* via PyAV and write 16 kHz mono 16-bit PCM WAV to *dest*.

    The resampler is given an explicit format / layout / rate so the
    output is deterministic regardless of input codec (webm/opus,
    ogg/opus, mp3, m4a, …).  The stdlib ``wave`` module handles WAV
    container writing — we intentionally do NOT rely on PyAV's
    muxer here because the WAV path is simple enough to stay
    dependency-light and the ``wave`` module produces a standard
    RIFF file that ``moonshine_voice.load_wav_file`` accepts without
    complaint.
    """
    container = av_module.open(str(source), mode="r")
    try:
        audio_stream = next((s for s in container.streams if s.type == "audio"), None)
        if audio_stream is None:
            raise RuntimeError(f"no audio stream found in {source.name}")
        resampler = av_module.AudioResampler(
            format="s16",
            layout="mono",
            rate=_TARGET_SAMPLE_RATE,
        )
        pcm = bytearray()
        for frame in container.decode(audio_stream):
            for rf in resampler.resample(frame):
                pcm.extend(rf.to_ndarray().tobytes())
        # Flush the resampler so any buffered tail samples are emitted.
        for rf in resampler.resample(None):
            pcm.extend(rf.to_ndarray().tobytes())
    finally:
        container.close()

    with wave.open(str(dest), "wb") as w:
        w.setnchannels(_TARGET_CHANNELS)
        w.setsampwidth(_TARGET_SAMPLE_WIDTH_BYTES)
        w.setframerate(_TARGET_SAMPLE_RATE)
        w.writeframes(bytes(pcm))
