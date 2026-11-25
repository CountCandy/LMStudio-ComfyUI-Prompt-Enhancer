import base64
import io

import numpy as np
from PIL import Image

from lmse import media


def decode(url):
    head, data = url.split(",", 1)
    return head, Image.open(io.BytesIO(base64.b64decode(data)))


def test_transparent_filled_white():
    img = np.zeros((1, 8, 8, 4), dtype=np.float32)  # black, fully transparent
    pil = media.tensor_to_pil(img)
    assert pil.getpixel((0, 0)) == (255, 255, 255)


def test_longest_side_scaled_down():
    img = np.random.rand(1, 600, 2000, 3).astype(np.float32)
    enc = media.encode_images(img, max_side=1000)
    assert len(enc) == 1 and (enc[0].width, enc[0].height) == (1000, 300)
    head, pil = decode(enc[0].data_url)
    assert head.startswith("data:image/") and pil.size == (1000, 300)
    assert enc[0].label == "[image 1: 1000x300]"


def test_large_png_becomes_jpeg():
    img = np.random.rand(1, 1536, 1536, 3).astype(np.float32)  # noise doesn't compress
    enc = media.encode_images(img)
    assert enc[0].format == "jpeg" and enc[0].data_url.startswith("data:image/jpeg;base64,")


def test_batch_first_or_all():
    batch = np.random.rand(3, 32, 48, 3).astype(np.float32)
    assert len(media.encode_images(batch, "first")) == 1
    all_ = media.encode_images(batch, "all")
    assert [e.label for e in all_] == ["[image 1: 48x32]", "[image 2: 48x32]", "[image 3: 48x32]"]


def test_frame_sampling():
    assert media.frame_indices(100, 8) == [0, 14, 28, 42, 57, 71, 85, 99]
    assert media.frame_indices(3, 8) == [0, 1, 2]
    frames = media.encode_frames(np.random.rand(48, 16, 16, 3).astype(np.float32), count=3, fps=24)
    assert [f.label.split(":")[0] for f in frames] == ["[frame at 0.00s", "[frame at 1.00s", "[frame at 1.96s"]


def test_audio_stub_wav():
    wave_in = {"waveform": np.zeros((1, 2, 44100), dtype=np.float32), "sample_rate": 44100}
    url = media.audio_to_wav_data_url(wave_in)
    raw = base64.b64decode(url.split(",", 1)[1])
    import wave

    with wave.open(io.BytesIO(raw)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 16000)
