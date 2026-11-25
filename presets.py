"""Saved instructions (presets): Markdown files with a short header.

    ---
    name: MOSS
    description: Turns a short idea into a MOSS prompt.
    user_template: "The user's idea: {words}"
    settings:              # optional starting values; the node's widgets still win
      temperature: 0.7
    slots:                 # optional extra fields shown on the node
      style: ""
    ---
    === words ===
    You are a prompt writer... (used when no picture is connected)
    === picture ===
    You describe pictures... (used when an image is connected and its node is on)

A preset with both sections switches by itself: words to text by default, picture to text when
an image arrives. Without section markers the whole body is one instruction, used as `mode` says.

Shipped presets live in this pack's presets/ folder. Your own live in
ComfyUI/user/default/lmstudio-prompt-enhancer/presets/ and win over a shipped one with the same name.
Files are re-read whenever they change, so edits apply without restarting ComfyUI.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml  # ComfyUI ships PyYAML
except ImportError:  # pragma: no cover - only outside ComfyUI
    yaml = None

PACK_DIR = os.path.dirname(os.path.abspath(__file__))
SHIPPED_DIR = os.path.join(PACK_DIR, "presets")
USER_SUBDIR = "lmstudio-prompt-enhancer"

MODES = ("words", "picture", "both")  # "auto" is set for presets with both sections
SECTION_RE = re.compile(r"(?im)^[ \t]*===[ \t]*(words|picture)[ \t]*===[ \t]*$")
BUILTIN_SLOTS = ("words", "length", "trigger")

FRONT_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
SLOT_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class PresetError(Exception):
    pass


@dataclass
class Preset:
    name: str
    instruction: str  # the words instruction (or the only one)
    mode: str = "both"  # words | picture | both | auto
    description: str = ""
    user_template: str = "{words}"
    picture_instruction: str | None = None  # from an === picture === section
    picture_user_template: str | None = None
    aliases: list[str] = field(default_factory=list)  # old names that still find this preset
    settings: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, str] = field(default_factory=dict)  # extra slots and their defaults
    lengths: dict[str, str] = field(default_factory=dict)  # overrides for short / medium / long wording
    checks: dict[str, Any] = field(default_factory=dict)
    response_format: dict | None = None
    negative: bool = False
    negative_marker: str = "Negative:"
    path: str = ""
    source: str = "shipped"  # shipped | user
    digest: str = ""

    def instruction_for(self, mode: str) -> str:
        if mode == "picture" and self.picture_instruction is not None:
            return self.picture_instruction
        return self.instruction

    def template_for(self, mode: str) -> str:
        if mode == "picture" and self.picture_user_template is not None:
            return self.picture_user_template
        return self.user_template

    def _texts(self) -> str:
        return "\n".join(t for t in (self.instruction, self.user_template, self.picture_instruction,
                                      self.picture_user_template) if t)

    def uses(self, slot: str) -> bool:
        return "{" + slot + "}" in self._texts()

    def summary(self) -> dict:
        return {
            "name": self.name, "mode": self.mode, "description": self.description,
            "slots": self.slots, "settings": self.settings, "source": self.source,
            "structured": self.response_format is not None, "negative": self.negative,
            "uses_length": self.uses("length"), "uses_trigger": self.uses("trigger"),
            "aliases": self.aliases,
        }


def user_dir() -> str:
    """ComfyUI/user/default/lmstudio-prompt-enhancer/presets (outside ComfyUI: ~/.lmstudio-prompt-enhancer)."""
    return os.path.join(data_dir(), "presets")


def data_dir() -> str:
    override = os.environ.get("LMSE_DATA_DIR")
    if override:
        return override
    try:
        import folder_paths  # ComfyUI

        return os.path.join(folder_paths.get_user_directory(), "default", USER_SUBDIR)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "." + USER_SUBDIR)


def _parse_header(text: str, path: str) -> dict:
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise PresetError(f"{os.path.basename(path)}: the header isn't valid YAML ({e}).") from e
    else:  # pragma: no cover
        data = {}
        for line in text.splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                k, v = line.split(":", 1)
                data[k.strip()] = v.split("#", 1)[0].strip().strip('"')
    if not isinstance(data, dict):
        raise PresetError(f"{os.path.basename(path)}: the header must be a list of key: value lines.")
    return data


def parse(text: str, path: str = "preset.md", source: str = "shipped") -> Preset:
    match = FRONT_RE.match(text)
    header = _parse_header(match.group(1), path) if match else {}
    body = text[match.end():] if match else text
    stem = os.path.splitext(os.path.basename(path))[0]
    name = str(header.get("name") or stem).strip()
    mode = str(header.get("mode") or "both").strip().lower()
    if mode not in MODES:
        raise PresetError(f"{os.path.basename(path)}: mode must be words, picture or both, not {mode!r}.")
    instruction, picture_instruction = _sections(body, path)
    if picture_instruction is not None:
        if instruction:
            mode = "auto"  # words to text by default, picture to text when an image is connected
        else:
            mode, instruction, picture_instruction = "picture", picture_instruction, None
    slots: dict[str, str] = {}
    raw_slots = header.get("slots") or {}
    if isinstance(raw_slots, list):
        raw_slots = {str(s): "" for s in raw_slots}
    if not isinstance(raw_slots, dict):
        raise PresetError(f"{os.path.basename(path)}: slots must be a list of names or name: default lines.")
    for k, v in raw_slots.items():
        k = str(k).strip()
        if not SLOT_NAME_RE.match(k):
            raise PresetError(f"{os.path.basename(path)}: slot name {k!r} must be lowercase letters, digits or _.")
        if k in BUILTIN_SLOTS:
            continue
        slots[k] = "" if v is None else str(v)
    response_format = header.get("response_format")
    if isinstance(response_format, str) and response_format.strip():
        import json

        try:
            response_format = json.loads(response_format)
        except json.JSONDecodeError as e:
            raise PresetError(f"{os.path.basename(path)}: response_format isn't valid JSON ({e}).") from e
    if response_format is not None and not isinstance(response_format, dict):
        raise PresetError(f"{os.path.basename(path)}: response_format must be a JSON schema object.")
    settings = header.get("settings") or {}
    checks = header.get("checks") or {}
    lengths = header.get("lengths") or {}
    for label, value in (("settings", settings), ("checks", checks), ("lengths", lengths)):
        if not isinstance(value, dict):
            raise PresetError(f"{os.path.basename(path)}: {label} must be key: value lines.")
    return Preset(
        name=name, instruction=instruction, mode=mode,
        description=str(header.get("description") or "").strip(),
        user_template=str(header.get("user_template") if header.get("user_template") is not None else "{words}"),
        picture_instruction=picture_instruction,
        picture_user_template=(str(header["picture_user_template"])
                               if header.get("picture_user_template") is not None else None),
        aliases=[str(a).strip() for a in _as_list(header.get("aliases")) if str(a).strip()],
        settings=dict(settings), slots=slots, lengths={str(k): str(v) for k, v in lengths.items()},
        checks=dict(checks), response_format=response_format,
        negative=bool(header.get("negative", False)),
        negative_marker=str(header.get("negative_marker") or "Negative:"),
        path=path, source=source, digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _sections(body: str, path: str) -> tuple[str, str | None]:
    """(words instruction, picture instruction or None). Text before the first marker counts as words."""
    parts = SECTION_RE.split(body)
    words = [parts[0]]
    picture: list[str] | None = None
    for kind, text in zip(parts[1::2], parts[2::2]):
        if kind.lower() == "picture":
            picture = (picture or []) + [text]
        else:
            words.append(text)
    clean = lambda chunks: "\n\n".join(c.strip("\r\n").rstrip() for c in chunks if c.strip()).strip("\r\n")
    return clean(words), (clean(picture) if picture is not None else None)


class Library:
    """Finds and caches presets; re-reads a file only when its size or time changes."""

    def __init__(self, shipped_dir: str = SHIPPED_DIR, user_dir_path: str | None = None):
        self.shipped_dir = shipped_dir
        self._user_dir = user_dir_path
        self._cache: dict[str, tuple[tuple[float, int], Preset | PresetError]] = {}

    @property
    def user_dir(self) -> str:
        return self._user_dir or user_dir()

    def _files(self) -> list[tuple[str, str]]:
        found = []
        for folder, source in ((self.shipped_dir, "shipped"), (self.user_dir, "user")):
            if not os.path.isdir(folder):
                continue
            for entry in sorted(os.listdir(folder), key=str.lower):
                if entry.lower().endswith(".md") and not entry.startswith("."):
                    found.append((os.path.join(folder, entry), source))
        return found

    def _load(self, path: str, source: str) -> Preset | PresetError:
        try:
            st = os.stat(path)
        except OSError as e:
            return PresetError(f"{path}: {e}")
        key = (st.st_mtime, st.st_size)
        cached = self._cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        try:
            with open(path, "r", encoding="utf-8") as f:
                result: Preset | PresetError = parse(f.read(), path, source)
        except (OSError, UnicodeDecodeError, PresetError) as e:
            result = e if isinstance(e, PresetError) else PresetError(f"{os.path.basename(path)}: {e}")
        self._cache[path] = (key, result)
        return result

    def scan(self) -> tuple[dict[str, Preset], list[str]]:
        """All presets by name (user files win), and problems found while reading."""
        presets: dict[str, Preset] = {}
        problems: list[str] = []
        by_stem: dict[str, str] = {}
        for path, source in self._files():
            result = self._load(path, source)
            if isinstance(result, PresetError):
                problems.append(str(result))
                continue
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            # A user file with the same file name replaces the shipped one even if its name changed.
            if source == "user" and stem in by_stem:
                presets.pop(by_stem[stem], None)
            presets[result.name] = result
            by_stem[stem] = result.name
        return presets, problems

    def names(self) -> list[str]:
        presets, _ = self.scan()
        return sorted(presets, key=lambda n: (presets[n].source != "shipped", n.lower())) or ["(no presets found)"]

    def get(self, name: str) -> Preset:
        presets, problems = self.scan()
        if name in presets:
            return presets[name]
        for preset in presets.values():  # an old name, e.g. "MOSS from words" is now "MOSS"
            if name in preset.aliases:
                return preset
        hint = f" Problems reading presets: {'; '.join(problems)}" if problems else ""
        raise PresetError(f"Preset {name!r} wasn't found in {self.shipped_dir} or {self.user_dir}.{hint}")


LIBRARY = Library()
