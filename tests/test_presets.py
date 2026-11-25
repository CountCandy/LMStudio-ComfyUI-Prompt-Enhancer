import os
import time

import pytest

from lmse import presets


def write(folder, name, text):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_shipped_presets_parse():
    lib = presets.Library(user_dir_path="/nonexistent")
    found, problems = lib.scan()
    assert not problems
    assert {"MOSS from words", "MOSS from picture", "Plain rewrite", "MOSS layout"} <= set(found)
    words = found["MOSS from words"]
    assert words.mode == "words" and "{words}" in words.user_template
    assert words.settings["max_tokens"] == 400
    assert found["MOSS from picture"].mode == "picture"
    layout = found["MOSS layout"]
    assert layout.response_format["type"] == "object" and layout.slots == {"width": "1024", "height": "1024"}


def test_parse_header_and_body():
    p = presets.parse("---\nname: X\nmode: both\nslots: [style, words]\n---\nDo {style}.\n", "x.md")
    assert p.name == "X" and p.mode == "both" and p.instruction == "Do {style}."
    assert p.slots == {"style": ""}  # built-in slots are not listed as extra fields


def test_no_header_uses_file_name():
    p = presets.parse("Just an instruction.", "/a/My preset.md")
    assert p.name == "My preset" and p.mode == "words" and p.user_template == "{words}"


def test_bad_mode_reported():
    with pytest.raises(presets.PresetError, match="mode must be"):
        presets.parse("---\nmode: video\n---\nx", "bad.md")


def test_user_preset_wins_and_reloads(tmp_path):
    shipped = tmp_path / "shipped"
    user = tmp_path / "user"
    write(shipped, "Plain.md", "---\nname: Plain\n---\nshipped")
    lib = presets.Library(str(shipped), str(user))
    assert lib.get("Plain").instruction == "shipped"
    path = write(user, "Plain.md", "---\nname: Plain\n---\nmine")
    assert lib.get("Plain").instruction == "mine" and lib.get("Plain").source == "user"
    first = lib.get("Plain").digest
    time.sleep(0.01)
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\nname: Plain\n---\nmine, edited")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert lib.get("Plain").instruction == "mine, edited" and lib.get("Plain").digest != first


def test_broken_file_listed_as_problem(tmp_path):
    write(tmp_path, "ok.md", "fine")
    write(tmp_path, "broken.md", "---\nmode: [\n---\nx")
    lib = presets.Library(str(tmp_path), str(tmp_path / "none"))
    found, problems = lib.scan()
    assert "ok" in found and len(problems) == 1
    with pytest.raises(presets.PresetError, match="wasn't found"):
        lib.get("broken")
