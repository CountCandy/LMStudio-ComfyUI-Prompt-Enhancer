"""Turns ComfyUI media into what LM Studio accepts: base64 data URLs.

Images are live. Video frames and audio go through the same path but are not wired to the
node yet (Gemma 4 31B has no audio input; video is just labelled frames)."""

from __future__ import annotations

import base64
import io
import wave
from dataclasses import dataclass

import numpy as np
from PIL import Image

DEFAULT_MAX_SIDE = 1536
PNG_LIMIT = 4 * 1024 * 1024  # bigger PNGs are sent as JPEG
JPEG_QUALITY = 92


@dataclass
class EncodedImage:
    data_url: str
    width: int
    height: int
    format: str
    label: str = ""


def _to_numpy(t) -> np.ndarray:
    if hasattr(t, "detach"):
        t = t.detach()
    if hasattr(t, "cpu"):
        t = t.cpu()
    if hasattr(t, "float") and hasattr(t, "numpy"):
        t = t.float().numpy()
    return np.asarray(t, dtype=np.float32)


def batch_size(images) -> int:
    if images is None:
        return 0
    shape = getattr(images, "shape", None)
    if shape is None:
        return len(images)
    return int(shape[0]) if len(shape) == 4 else 1


def tensor_to_pil(image) -> Image.Image:
    """One ComfyUI image ([H, W, C] floats in 0..1) to a PIL picture, transparency filled white."""
    arr = _to_numpy(image)
    if arr.ndim == 4:
        arr = arr[0]
    if arr.ndim == 2:
        arr = arr[..., None]
    arr = np.clip(arr, 0.0, 1.0)
    channels = arr.shape[-1]
    if channels == 1:
        arr = np.repeat(arr, 3, axis=-1)
    elif channels == 2:  # gray + alpha
        arr = np.concatenate([np.repeat(arr[..., :1], 3, axis=-1), arr[..., 1:2]], axis=-1)
    if arr.shape[-1] == 4:
        rgb, alpha = arr[..., :3], arr[..., 3:4]
        arr = rgb * alpha + (1.0 - alpha)  # white where transparent
    elif arr.shape[-1] > 4:
        arr = arr[..., :3]
    return Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8), "RGB")


def shrink(img: Image.Image, max_side: int) -> Image.Image:
    if max_side and max(img.size) > max_side:
        scale = max_side / float(max(img.size))
        size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(size, Image.LANCZOS)
    return img


def encode_pil(img: Image.Image) -> tuple[str, str]:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    data, fmt = buf.getvalue(), "png"
    if len(data) > PNG_LIMIT:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY)
        data, fmt = buf.getvalue(), "jpeg"
    return f"data:image/{fmt};base64," + base64.b64encode(data).decode("ascii"), fmt


def encode_images(images, which: str = "first", max_side: int = DEFAULT_MAX_SIDE) -> list[EncodedImage]:
    """A ComfyUI IMAGE batch to data URLs: the first image only, or all of them in order."""
    n = batch_size(images)
    if n == 0:
        return []
    indices = range(n) if which == "all" else range(1)
    out = []
    for k, i in enumerate(indices, start=1):
        frame = images[i] if n > 1 or getattr(images, "ndim", 0) == 4 else images
        img = shrink(tensor_to_pil(frame), max_side)
        url, fmt = encode_pil(img)
        out.append(EncodedImage(url, img.width, img.height, fmt, f"[image {k}: {img.width}x{img.height}]"))
    return out


# -- video (not wired to the node yet) ---------------------------------------------------------


def frame_indices(total: int, count: int) -> list[int]:
    """count frames spread evenly over total, first and last included."""
    if total <= 0 or count <= 0:
        return []
    if count >= total:
        return list(range(total))
    if count == 1:
        return [0]
    return sorted({round(i * (total - 1) / (count - 1)) for i in range(count)})


def encode_frames(frames, count: int = 8, fps: float = 24.0, max_side: int = DEFAULT_MAX_SIDE) -> list[EncodedImage]:
    """Samples frames from an IMAGE batch and labels each with its time, since Gemma 4 reads video as frames."""
    out = []
    for i in frame_indices(batch_size(frames), count):
        img = shrink(tensor_to_pil(frames[i]), max_side)
        url, fmt = encode_pil(img)
        seconds = i / fps if fps else 0.0
        out.append(EncodedImage(url, img.width, img.height, fmt, f"[frame at {seconds:.2f}s: {img.width}x{img.height}]"))
    return out


# -- audio (stub: Gemma 4 31B has no audio input) ----------------------------------------------


def audio_to_wav_data_url(audio: dict, rate: int = 16000) -> str:
    """ComfyUI AUDIO ({"waveform": [B, C, T], "sample_rate": n}) to a 16 kHz mono WAV data URL."""
    wave_data = _to_numpy(audio["waveform"])
    if wave_data.ndim == 3:
        wave_data = wave_data[0]
    if wave_data.ndim == 2:
        wave_data = wave_data.mean(axis=0)
    src_rate = int(audio.get("sample_rate") or rate)
    if src_rate != rate and wave_data.size:
        duration = wave_data.size / src_rate
        n = max(1, int(round(duration * rate)))
        wave_data = np.interp(np.linspace(0, wave_data.size - 1, n), np.arange(wave_data.size), wave_data)
    pcm = (np.clip(wave_data, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
