import random

from lmse import prompt_tools as pt


def test_fill_drops_empty_slot_lines():
    tpl = "Describe this.\nThe user's changes: {words}\nTriggers: {trigger}"
    assert pt.fill(tpl, {"words": "", "trigger": "ohwx"}) == "Describe this.\nTriggers: ohwx"
    assert pt.fill("{words}", {"words": ""}) == ""


def test_fill_leaves_unknown_braces():
    assert pt.fill('Return {"a": 1} for {words}', {"words": "x"}) == 'Return {"a": 1} for x'
    assert pt.fill("{style} {words}", {"words": "x"}) == "{style} x"


def test_choices_follow_the_seed():
    a = pt.resolve_choices("a {red|green|blue} {cat|dog}", random.Random(1))
    b = pt.resolve_choices("a {red|green|blue} {cat|dog}", random.Random(1))
    assert a == b and "{" not in a
    assert pt.resolve_choices("{a|{b|c}}", random.Random(3)) in ("a", "b", "c")
    assert pt.resolve_choices("keep {words}", random.Random(0)) == "keep {words}"


def test_slot_lines():
    assert pt.parse_slot_lines("style: noir\ncharacter: a tall man\n  in a hat\n") == {
        "style": "noir", "character": "a tall man\n  in a hat"}


def test_protected_texts():
    found = pt.protected_texts('a sign that says "OPEN 24/7" and “Café”', "ohwx woman, sks")
    assert found == ["OPEN 24/7", "Café", "ohwx woman", "sks"]


def test_checks():
    good = "An oil painting of a fox in a red scarf reading a book under a lamp."
    assert pt.check_prompt(good, ["red scarf"], (5, 40)).ok
    r = pt.check_prompt("Lighting: dim. A fox with no tail.", ["blue hat"], (20, 40))
    joined = " | ".join(r.problems)
    assert "blue hat" in joined and "words" in joined and "negative phrasing: no" in joined and "labels" in joined


def test_negation_inside_protected_text_is_fine():
    assert pt.check_prompt('A sign reading "NO PARKING" on a brick wall.', ["NO PARKING"], None).ok


def test_clean_reply():
    assert pt.clean_reply('  "A fox."  ') == "A fox."
    assert pt.clean_reply("```json\n{\"a\": 1}\n```", structured=True) == '{"a": 1}'
    assert pt.clean_reply('He said "hi" to "you"') == 'He said "hi" to "you"'


def test_split_negative():
    assert pt.split_negative("A fox.\n\nNegative: blurry, low quality") == ("A fox.", "blurry, low quality")
    assert pt.split_negative("A fox.") == ("A fox.", "")


class FakeClip:
    def tokenize(self, text, return_word_ids=False):
        words = text.split()
        return {"qwen35_9b": [[(1, 1.0, 0)] + [(i + 10, 1.0, i + 1) for i in range(len(words))] + [(0, 1.0, 0)] * 3]}


def test_count_tokens():
    assert pt.count_tokens(FakeClip(), "a b c") == 3
    assert pt.count_tokens(None, "a") is None


class TemplateClip:
    """Like MOSS's tokenizer: chat-template tokens around the prompt carry word ids too."""

    def tokenize(self, text, return_word_ids=False):
        template = [(900 + i, 1.0, 1) for i in range(10)]
        return {"qwen35_9b": [template + [(i + 10, 1.0, 1) for i in range(len(text.split()))]]}


def test_count_tokens_leaves_out_the_chat_template():
    assert pt.count_tokens(TemplateClip(), "a b c d") == 4
