"""Building the request text and checking the reply. Pure functions, no ComfyUI."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass

# MOSS-Image was trained on captions of about 21, 77 and 235 tokens. These are the words that
# go in a preset's {length} slot, and the word range the prompt check expects for each.
LENGTHS: dict[str, tuple[str, tuple[int, int]]] = {
    "short": ("one sentence of about 15 words", (6, 30)),
    "medium": ("about 55 words", (30, 95)),
    "long": ("about 150 to 200 words", (110, 260)),
}
LENGTH_NAMES = tuple(LENGTHS)

SLOT_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
CHOICE_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
QUOTED_RE = re.compile(r"\"([^\"\n]+)\"|“([^”\n]+)”")
NEGATION_RE = re.compile(r"\b(no|not|never|without|none|nothing|nobody|\w+n't)\b", re.IGNORECASE)
LABEL_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s|\*\*[^*\n]{1,40}\*\*|[-*•]\s|(?:[A-Z][\w'&/-]*)(?:\s+[\w'&/-]+){0,3}:\s)")
FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def word_count(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def resolve_choices(text: str, rng: random.Random) -> str:
    """{red|green|blue} picks one option (ComfyUI's wildcard syntax). Nested braces work."""
    for _ in range(100):
        new = CHOICE_RE.sub(lambda m: rng.choice(m.group(1).split("|")), text)
        if new == text:
            return new
        text = new
    return text


def parse_slot_lines(text: str) -> dict[str, str]:
    """'name: value' per line, as the node stores extra slot values."""
    values: dict[str, str] = {}
    current = None
    for line in (text or "").splitlines():
        m = re.match(r"^([a-z_][a-z0-9_]*)\s*:\s?(.*)$", line)
        if m:
            current = m.group(1)
            values[current] = m.group(2)
        elif current is not None and line.strip():
            values[current] += "\n" + line  # a value can run over several lines
    return {k: v.strip() for k, v in values.items()}


def fill(template: str, values: dict[str, str], drop_empty_lines: bool = True) -> str:
    """Puts values in {slot}s. A line whose slot is empty is dropped cleanly.

    Only names present in `values` are touched, so JSON examples or other braces stay as written."""
    out_lines = []
    for line in template.splitlines():
        names = [n for n in SLOT_RE.findall(line) if n in values]
        if drop_empty_lines and names and any(not str(values[n]).strip() for n in names):
            # Drop the line only when an empty slot would leave it saying nothing useful.
            stripped = SLOT_RE.sub(lambda m: "" if m.group(1) in values and not str(values[m.group(1)]).strip() else m.group(0), line)
            if not any(str(values[n]).strip() for n in names):
                continue
            line = stripped
        out_lines.append(SLOT_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), line))
    return "\n".join(out_lines).strip("\n")


def length_text(length: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and length in overrides:
        return overrides[length]
    return LENGTHS.get(length, ("", (0, 0)))[0]


def protected_texts(words: str, trigger_words: str) -> list[str]:
    """Text that must come back letter for letter: anything in double quotes, and trigger words."""
    found = [a or b for a, b in QUOTED_RE.findall(words or "")]
    found += [t.strip() for t in re.split(r"[,\n]", trigger_words or "") if t.strip()]
    seen, out = set(), []
    for t in found:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


@dataclass
class CheckResult:
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def check_prompt(prompt: str, protected: list[str], word_range: tuple[int, int] | None,
                 no_negation: bool = True, no_labels: bool = True) -> CheckResult:
    problems = []
    missing = [t for t in protected if t not in prompt]
    if missing:
        problems.append("changed or dropped: " + ", ".join(f'"{t}"' for t in missing))
    if word_range:
        n = word_count(prompt)
        lo, hi = word_range
        if n < lo or n > hi:
            problems.append(f"{n} words (expected {lo}-{hi})")
    if no_negation:
        scrubbed = prompt
        for t in protected:  # words the user asked for are never flagged
            scrubbed = scrubbed.replace(t, " ")
        hits = sorted({m.group(0).lower() for m in NEGATION_RE.finditer(scrubbed)})
        if hits:
            problems.append("negative phrasing: " + ", ".join(hits))
    if no_labels and LABEL_RE.search(prompt):
        problems.append("labels, headings or bullets")
    return CheckResult(problems)


def clean_reply(text: str, structured: bool = False) -> str:
    """Trims the reply; removes a code fence or quotes wrapped around the whole of it."""
    text = (text or "").strip()
    m = FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    if structured:
        try:
            return json.dumps(json.loads(text), ensure_ascii=False)
        except json.JSONDecodeError:
            return text
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'" and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    return text


def split_negative(text: str, marker: str = "Negative:") -> tuple[str, str]:
    """Splits 'positive … Negative: …' into the two parts (marker matched case-insensitively)."""
    m = re.search(r"(?im)^\s*(?:\*\*)?" + re.escape(marker.rstrip(":")) + r"(?:\*\*)?\s*:\s*", text)
    if not m:
        return text.strip(), ""
    return text[: m.start()].strip(), text[m.end():].strip()


def count_tokens(clip, text: str) -> int | None:
    """Prompt length in the text encoder's own tokenizer (MOSS: Qwen3.5), template tokens excluded."""
    if clip is None or not text:
        return None
    try:
        tokens = clip.tokenize(text, return_word_ids=True)
    except TypeError:
        tokens = clip.tokenize(text)
    best = 0
    for batches in tokens.values() if isinstance(tokens, dict) else [tokens]:
        n = 0
        for batch in batches:
            for item in batch:
                if isinstance(item, (tuple, list)) and len(item) >= 3:
                    n += 1 if item[2] else 0
                else:
                    n += 1
        best = max(best, n)
    return best
