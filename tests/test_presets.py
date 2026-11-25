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
    assert set(found) == {"MOSS", "Plain rewrite", "MOSS layout"}
    moss = found["MOSS"]
    assert moss.mode == "auto" and moss.settings["max_tokens"] == 400
    assert moss.instruction.startswith("You are a prompt writer") and "Turn the user's idea" in moss.instruction
    assert "Look at the picture" in moss.picture_instruction and "Turn the user's idea" not in moss.picture_instruction
    assert moss.template_for("words") == "The user's idea: {words}"
    assert moss.template_for("picture").startswith("Describe this picture")
    layout = found["MOSS layout"]
    assert layout.response_format["type"] == "object" and layout.slots == {"width": "1024", "height": "1024"}


def test_old_names_still_find_moss():
    lib = presets.Library(user_dir_path="/nonexistent")
    assert lib.get("MOSS from words").name == "MOSS"
    assert lib.get("MOSS from picture").name == "MOSS"
    assert "MOSS from words" not in lib.names()


def test_sections():
    p = presets.parse("---\nname: S\n---\nShared words text.\n=== picture ===\nDescribe it.\n", "s.md")
    assert (p.mode, p.instruction, p.picture_instruction) == ("auto", "Shared words text.", "Describe it.")
    p = presets.parse("=== words ===\nW\n\n=== PICTURE ===\nP", "s.md")
    assert (p.instruction, p.picture_instruction) == ("W", "P")
    p = presets.parse("=== picture ===\nOnly pictures.", "s.md")
    assert (p.mode, p.instruction, p.picture_instruction) == ("picture", "Only pictures.", None)
    p = presets.parse("No sections; a === picture === in the middle of a line is just text.", "s.md")
    assert p.mode == "both" and p.picture_instruction is None
    assert p.instruction_for("picture") == p.instruction


def test_parse_header_and_body():
    p = presets.parse("---\nname: X\nmode: both\nslots: [style, words]\n---\nDo {style}.\n", "x.md")
    assert p.name == "X" and p.mode == "both" and p.instruction == "Do {style}."
    assert p.slots == {"style": ""}  # built-in slots are not listed as extra fields


def test_no_header_uses_file_name():
    p = presets.parse("Just an instruction.", "/a/My preset.md")
    assert p.name == "My preset" and p.mode == "both" and p.user_template == "{words}"


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
