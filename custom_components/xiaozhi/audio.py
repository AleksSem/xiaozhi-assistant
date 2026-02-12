"""Audio utilities for Xiaozhi integration.

Handles binary WebSocket frame packing/unpacking (Protocol V3)
and opus ↔ PCM conversion via FFmpeg subprocess.
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from collections.abc import AsyncIterator

from .const import (
    AUDIO_CHANNELS,
    AUDIO_FRAME_DURATION_MS,
    AUDIO_SAMPLE_RATE_INPUT,
    AUDIO_SAMPLE_RATE_OUTPUT,
    BINARY_FRAME_TYPE_AUDIO,
)

_LOGGER = logging.getLogger(__name__)

# Binary frame format: type(u8) | reserved(u8) | size(u16 BE) | payload
_FRAME_HEADER = struct.Struct(">BBH")

# OGG CRC-32 lookup table (polynomial 0x04C11DB7, no reflection)
_OGG_CRC_TABLE = [0] * 256
for _i in range(256):
    _r = _i << 24
    for _ in range(8):
        _r = ((_r << 1) ^ 0x04C11DB7) if _r & 0x80000000 else _r << 1
    _OGG_CRC_TABLE[_i] = _r & 0xFFFFFFFF


def _ogg_crc32(data: bytes) -> int:
    """Compute OGG-specific CRC-32."""
    crc = 0
    for b in data:
        crc = ((_ogg_crc32_lookup(crc, b))) & 0xFFFFFFFF
    return crc


def _ogg_crc32_lookup(crc: int, byte: int) -> int:
    return ((crc << 8) ^ _OGG_CRC_TABLE[((crc >> 24) & 0xFF) ^ byte])


def _build_ogg_page(
    serial: int,
    page_seq: int,
    granule: int,
    flags: int,
    segments_data: list[bytes],
) -> bytes:
    """Build a single OGG page from segment data.

    flags: 0x02=BOS, 0x04=EOS
    """
    # Build segment table: each segment can be at most 255 bytes.
    # Packets > 255 bytes need multiple 255-byte segments + a final < 255 segment.
    segment_table = bytearray()
    body = bytearray()
    for seg in segments_data:
        data_len = len(seg)
        while data_len >= 255:
            segment_table.append(255)
            data_len -= 255
        segment_table.append(data_len)
        body.extend(seg)

    num_segments = len(segment_table)

    # Header with CRC placeholder = 0
    header = struct.pack(
        "<4sBBqIIIB",
        b"OggS",     # capture pattern
        0,            # version
        flags,
        granule,      # granule position (signed i64 LE)
        serial,       # stream serial number
        page_seq,     # page sequence number
        0,            # CRC placeholder
        num_segments,
    )

    page_no_crc = header + bytes(segment_table) + bytes(body)
    crc = _ogg_crc32(page_no_crc)

    # Patch CRC at offset 22
    page = bytearray(page_no_crc)
    struct.pack_into("<I", page, 22, crc)
    return bytes(page)


def _build_ogg_opus_stream(
    opus_packets: list[bytes],
    sample_rate: int,
    channels: int,
) -> bytes:
    """Build a complete OGG/Opus stream from raw opus packets.

    Inverse of _parse_ogg_opus_packets(): wraps raw packets into a valid
    OGG/Opus container that FFmpeg can decode.
    """
    serial = 0x58495A48  # "XIZH"
    pages: list[bytes] = []

    # Page 0 (BOS): OpusHead
    opus_head = struct.pack(
        "<8sBBHIhB",
        b"OpusHead",
        1,              # version
        channels,
        312,            # pre-skip (samples at 48kHz)
        sample_rate,    # original sample rate
        0,              # output gain
        0,              # channel mapping family
    )
    pages.append(_build_ogg_page(serial, 0, 0, 0x02, [opus_head]))

    # Page 1: OpusTags
    vendor = b"xiaozhi"
    opus_tags = struct.pack("<8sI", b"OpusTags", len(vendor)) + vendor
    opus_tags += struct.pack("<I", 0)  # 0 user comments
    pages.append(_build_ogg_page(serial, 1, 0, 0, [opus_tags]))

    # Audio pages: one opus packet per page
    # Opus always works at 48kHz internally; 20ms frame = 960 samples
    granule = 0
    samples_per_frame = 960  # 20ms @ 48kHz
    for i, packet in enumerate(opus_packets):
        granule += samples_per_frame
        is_last = i == len(opus_packets) - 1
        flags = 0x04 if is_last else 0
        pages.append(_build_ogg_page(serial, i + 2, granule, flags, [packet]))

    return b"".join(pages)


def pack_audio_frame(opus_data: bytes) -> bytes:
    """Pack opus data into a binary WebSocket frame (Protocol V3)."""
    header = _FRAME_HEADER.pack(BINARY_FRAME_TYPE_AUDIO, 0, len(opus_data))
    return header + opus_data


def unpack_audio_frame(data: bytes) -> bytes | None:
    """Unpack a binary WebSocket frame, returning the opus payload.

    Returns None if frame type is not audio or data is malformed.
    """
    if len(data) < _FRAME_HEADER.size:
        return None
    frame_type, _, size = _FRAME_HEADER.unpack_from(data)
    if frame_type != BINARY_FRAME_TYPE_AUDIO:
        return None
    payload = data[_FRAME_HEADER.size : _FRAME_HEADER.size + size]
    if len(payload) != size:
        _LOGGER.warning("Audio frame truncated: expected %d, got %d", size, len(payload))
        return None
    return payload


async def _parse_ogg_opus_packets(
    stdout: asyncio.StreamReader,
) -> AsyncIterator[bytes]:
    """Parse OGG/Opus stream and yield individual raw opus packets.

    OGG page structure:
      "OggS" (4b) | version (1b) | flags (1b) | granule (8b) |
      serial (4b) | page_seq (4b) | crc (4b) | num_segments (1b) |
      segment_table (num_segments bytes) | segment_data ...

    First 2 pages are OpusHead + OpusTags headers — skipped.
    Segments of 255 bytes are "continued"; <255 terminates the packet.
    """
    page_index = 0
    pending_parts: list[bytes] = []

    while True:
        try:
            sync = await stdout.readexactly(4)
        except asyncio.IncompleteReadError:
            break

        if sync != b"OggS":
            _LOGGER.warning("OGG sync lost, expected 'OggS' got %r", sync)
            break

        try:
            header_rest = await stdout.readexactly(23)
        except asyncio.IncompleteReadError:
            break

        num_segments = header_rest[22]

        try:
            segment_table = await stdout.readexactly(num_segments)
        except asyncio.IncompleteReadError:
            break

        total_data = sum(segment_table)
        try:
            data = await stdout.readexactly(total_data)
        except asyncio.IncompleteReadError:
            break

        page_index += 1

        # Skip OpusHead (page 1) and OpusTags (page 2)
        if page_index <= 2:
            continue

        # Extract opus packets from segments
        offset = 0
        for seg_len in segment_table:
            pending_parts.append(data[offset : offset + seg_len])
            offset += seg_len
            if seg_len < 255:
                packet = b"".join(pending_parts)
                pending_parts = []
                if packet:
                    yield packet

    # Flush any remaining partial packet
    if pending_parts:
        packet = b"".join(pending_parts)
        if packet:
            yield packet


_VALID_SAMPLE_RATES = frozenset({8000, 16000, 24000, 48000})
_VALID_CHANNELS = frozenset({1, 2})


async def pcm_to_opus_frames(
    pcm_stream: AsyncIterator[bytes],
    sample_rate: int = AUDIO_SAMPLE_RATE_INPUT,
    channels: int = AUDIO_CHANNELS,
    frame_duration_ms: int = AUDIO_FRAME_DURATION_MS,
) -> AsyncIterator[bytes]:
    """Convert raw PCM audio stream to individual raw opus packets via FFmpeg.

    Yields individual opus packets suitable for packing into binary WS frames.
    HA sends raw PCM (s16le); we specify format/rate/channels explicitly.
    FFmpeg outputs OGG/Opus container; we parse it to extract raw packets.
    """
    if sample_rate not in _VALID_SAMPLE_RATES:
        raise ValueError(f"Invalid sample rate: {sample_rate}")
    if channels not in _VALID_CHANNELS:
        raise ValueError(f"Invalid channels: {channels}")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "32k",
        "-frame_duration", str(frame_duration_ms),
        "-application", "voip",
        "-vbr", "on",
        "-f", "opus",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _feed_stdin() -> None:
        assert proc.stdin is not None
        try:
            async for chunk in pcm_stream:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        finally:
            proc.stdin.close()
            await proc.stdin.wait_closed()

    feed_task = asyncio.create_task(_feed_stdin())

    try:
        assert proc.stdout is not None
        async for opus_packet in _parse_ogg_opus_packets(proc.stdout):
            yield opus_packet
    finally:
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass
        if proc.stderr:
            stderr_data = await proc.stderr.read()
            if stderr_data:
                _LOGGER.error(
                    "FFmpeg pcm→opus: %s",
                    stderr_data.decode(errors="replace").strip(),
                )
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _LOGGER.warning("FFmpeg process did not exit after kill")


async def opus_frames_to_wav(
    opus_frames: list[bytes],
    sample_rate: int = AUDIO_SAMPLE_RATE_OUTPUT,
    channels: int = AUDIO_CHANNELS,
) -> bytes | None:
    """Decode a list of raw opus frames to a WAV file via FFmpeg.

    The opus_frames are the payloads extracted from binary WebSocket frames
    (already unpacked from the Protocol V3 framing).
    Returns WAV file bytes, or None on decode failure.
    """
    if not opus_frames:
        return b""

    # Wrap raw opus packets into a valid OGG/Opus container for FFmpeg
    opus_data = _build_ogg_opus_stream(opus_frames, sample_rate, channels)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "ogg",
        "-i", "pipe:0",
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_data, stderr_data = await proc.communicate(input=opus_data)

    if proc.returncode != 0:
        _LOGGER.error("FFmpeg opus decode failed: %s", stderr_data.decode(errors="replace"))
        return None

    # Wrap raw PCM in WAV container
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(stdout_data)

    return wav_buffer.getvalue()


def generate_silence_wav(
    duration_ms: int = 500,
    sample_rate: int = AUDIO_SAMPLE_RATE_OUTPUT,
    channels: int = AUDIO_CHANNELS,
) -> bytes:
    """Generate a short silence WAV file.

    Used as TTS fallback when no cached audio is available.
    """
    num_frames = sample_rate * duration_ms // 1000
    silence_data = b"\x00\x00" * num_frames * channels  # 16-bit silence

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence_data)

    return wav_buffer.getvalue()
