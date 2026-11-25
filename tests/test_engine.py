import json
import os

import numpy as np
import pytest

from lmse import client as lm
from lmse import engine, presets

LIB = presets.Library(user_dir_path="/nonexistent")
GOOD = ("An oil painting in soft morning light, seen from a low angle. A small red fox wearing a green scarf "
        "sits upright and reads an old leather book. Around it, a quiet forest clearing glows with golden "
        "dew on tall grass, while pale mist drifts between birch trunks and a narrow stream winds past "
        "mossy stones toward distant blue hills under a clear sky.")


def run(fake, preset="MOSS", image=None, **kw):
    kw.setdefault("words", "a fox reading")
    kw.setdefault("length", "medium")
    opts = engine.options(model="google/gemma-4-31b", server_url=fake.url, stream=False, **kw)
    return engine.Runner(opts, LIB.get(preset), image=image).run()


def test_words_run_builds_exact_request(fake):
    fake.replies = ["ohwx " + GOOD]
    result = run(fake, words="a fox reading", trigger_words="ohwx")
    job = result.jobs[0]
    assert job.prompt == "ohwx " + GOOD and not job.warnings
    path, body = fake.requests[-1]
    system = body["messages"][0]["content"]
    assert "about 55 words" in system and "near the start: ohwx" in system
    assert body["messages"][1]["content"] == "The user's idea: a fox reading"
    assert body["temperature"] == 0.7 and body["max_tokens"] == 400 and body["seed"] == 0
    assert body["reasoning_effort"] == "none"
    assert "top_p" not in body  # ticks off: LM Studio's own settings apply
    assert json.loads(job.request)["model"] == "google/gemma-4-31b"
    assert "google/gemma-4-31b" in job.info and "/v1/chat/completions" in job.info


def test_empty_words_stops_before_calling(fake):
    with pytest.raises(engine.UserError, match="Type your idea"):
        run(fake, words="  ")
    assert not fake.requests


def test_picture_only_preset_needs_image(fake):
    p = presets.parse("---\nname: P\nmode: picture\n---\nDescribe.", "p.md")
    opts = engine.options(model="google/gemma-4-31b", server_url=fake.url, words="")
    with pytest.raises(engine.UserError, match="connect an image"):
        engine.Runner(opts, p).run()
    assert not fake.requests


def test_moss_defaults_to_words_and_switches_with_an_image(fake):
    fake.replies = [GOOD, GOOD]
    words_job = run(fake, words="a fox reading").jobs[0]
    body = fake.requests[-1][1]
    assert "Turn the user's idea" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "The user's idea: a fox reading"
    assert words_job.info.startswith("words → text")
    img = np.random.rand(1, 32, 32, 3).astype(np.float32)
    pic_job = run(fake, words="", image=img).jobs[0]
    body = fake.requests[-1][1]
    assert "Look at the picture" in body["messages"][0]["content"]
    assert body["messages"][1]["content"][0]["text"] == "Describe this picture as a prompt."
    assert pic_job.info.startswith("picture → text")


def test_no_image_and_no_words_asks_for_words(fake):
    with pytest.raises(engine.UserError, match="words box"):
        run(fake, words="")


def test_idea_socket_replaces_words_box(fake):
    fake.replies = [GOOD]
    job = run(fake, words="ignored box text", idea="a heron at dawn").jobs[0]
    assert fake.requests[-1][1]["messages"][1]["content"] == "The user's idea: a heron at dawn"
    assert "idea from socket" in job.info and job.idea == "a heron at dawn"


def test_idea_socket_lines_and_empty(fake):
    fake.replies = [GOOD, GOOD]
    result = run(fake, idea="a fox\na bear", one_per_line=True, checks=False)
    assert sorted(j.idea for j in result.jobs) == ["a bear", "a fox"]
    with pytest.raises(engine.UserError, match="idea socket is empty"):
        run(fake, words="box text", idea="  ")


def test_idea_with_picture_is_the_change_list(fake):
    fake.replies = [GOOD]
    img = np.random.rand(1, 32, 32, 3).astype(np.float32)
    run(fake, words="", idea="make it night", image=img)
    content = fake.requests[-1][1]["messages"][1]["content"]
    assert content[0]["text"] == "Describe this picture as a prompt.\nThe user's changes: make it night"


def test_picture_run_sends_image_and_drops_empty_changes_line(fake):
    fake.replies = [GOOD]
    img = np.random.rand(1, 64, 96, 3).astype(np.float32)
    job = run(fake, words="", image=img).jobs[0]
    content = fake.requests[-1][1]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "Describe this picture as a prompt."}
    assert content[1]["type"] == "image_url"
    assert "[image 1: 96x64]" in job.request


def test_both_mode_picks_by_image(fake):
    fake.replies = ["A tidy rewrite of the idea."]
    run(fake, preset="Plain rewrite", words="cat on mat")
    assert isinstance(fake.requests[-1][1]["messages"][1]["content"], str)


def test_model_not_loaded_stops(fake):
    with pytest.raises(lm.ModelNotLoaded):
        engine.Runner(engine.options(model="qwen/qwen3-8b", server_url=fake.url, words="x"), LIB.get("MOSS")).run()


def test_pass_through_on_error(fake):
    opts = engine.options(model="qwen/qwen3-8b", server_url=fake.url, words="my idea", on_error=engine.ON_ERROR_PASS)
    result = engine.Runner(opts, LIB.get("MOSS")).run()
    assert result.passed_through and result.jobs[0].prompt == "my idea"
    assert "Passed through" in result.warnings[0]


def test_check_failure_retries_once_with_new_seed(fake):
    fake.replies = ["Lighting: dim. A fox with no tail.", GOOD]
    job = run(fake, words="a fox", seed=5).jobs[0]
    assert job.prompt == GOOD and not job.warnings
    seeds = [b["seed"] for _, b in fake.requests if "seed" in b]
    assert seeds[0] == 5 and seeds[1] != 5
    assert "retried" in job.info


def test_check_failure_kept_as_warning(fake):
    fake.replies = ["A fox with no tail.", "A fox with no tail."]
    job = run(fake, words="a fox").jobs[0]
    assert any(w.startswith("Check:") for w in job.warnings)


def test_protected_text_checked(fake):
    fake.replies = [GOOD, GOOD]
    job = run(fake, words='a fox holding a sign that says "HELLO"').jobs[0]
    assert any('"HELLO"' in w for w in job.warnings)


def test_cut_off_reply_stops(fake):
    fake.finish_reason = "length"
    with pytest.raises(lm.LMStudioError, match="hit the token limit"):
        run(fake)


def test_thinking_budget_added(fake):
    fake.replies = [GOOD]
    fake.thinking = "I should mention the scarf."
    job = run(fake, reasoning="medium", thinking_budget=600).jobs[0]
    body = fake.requests[-1][1]
    assert body["max_tokens"] == 1000 and body["reasoning_effort"] == "medium"
    assert job.thinking == "I should mention the scarf."


def test_lines_and_variations_make_lists(fake):
    fake.replies = [GOOD] * 6
    result = run(fake, words="a fox\n\na bear\n", one_per_line=True, variations=2, seed=10, checks=False)
    assert len(result.jobs) == 4
    step = engine.VARIATION_STEP
    assert sorted(j.seed for j in result.jobs) == [10, 10 + step, 10 + 2 * step, 10 + 3 * step]
    # the next queue with seed "increment" (11) shares no seed with this one
    assert not {11 + k * step for k in range(4)} & {j.seed for j in result.jobs}
    assert sorted(j.idea for j in result.jobs) == ["a bear", "a bear", "a fox", "a fox"]


def test_dynamic_choices_resolved_by_seed(fake):
    fake.replies = [GOOD, GOOD]
    run(fake, words="a {red|blue} fox", seed=3, checks=False)
    first = fake.requests[-1][1]["messages"][1]["content"]
    run(fake, words="a {red|blue} fox", seed=3, checks=False)
    assert fake.requests[-1][1]["messages"][1]["content"] == first and "{" not in first


def test_structured_layout_preset(fake):
    layout = {"summary": {"caption": "A poster."}, "items": [], "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
    fake.replies = [json.dumps(layout)]
    job = run(fake, preset="MOSS layout", words="a poster", slots="width: 1344\nheight: 768").jobs[0]
    body = fake.requests[-1][1]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["required"][0] == "summary"
    assert "Canvas: 1344x768 pixels." in body["messages"][1]["content"]
    assert "-1344/2" in body["messages"][0]["content"]
    assert json.loads(job.prompt) == layout


def test_extra_json_merged_and_validated(fake):
    fake.replies = [GOOD]
    run(fake, extra_json='{"presence_penalty": 0.3}')
    assert fake.requests[-1][1]["presence_penalty"] == 0.3
    with pytest.raises(engine.UserError, match="extra_json"):
        run(fake, extra_json="{nope")


def test_instruction_override(fake):
    fake.replies = ["A tidy short prompt about a fox reading quietly in a sunny garden today."]
    run(fake, instruction_override="Only say hi.", checks=False)
    assert fake.requests[-1][1]["messages"][0]["content"] == "Only say hi."


def test_history_written(fake, data_dir):
    fake.replies = [GOOD]
    run(fake, words="a fox")
    with open(os.path.join(data_dir, "history.jsonl"), encoding="utf-8") as f:
        entry = json.loads(f.readline())
    assert entry["prompt"] == GOOD and entry["preset"] == "MOSS" and entry["words"] == "a fox"


def test_negative_split(fake, tmp_path):
    p = presets.parse("---\nname: N\nnegative: true\nchecks: {no_negation: false}\n---\nWrite.", "n.md")
    fake.replies = ["A fox in a field at dawn.\nNegative: blurry, watermark"]
    opts = engine.options(model="google/gemma-4-31b", server_url=fake.url, words="fox", stream=False)
    job = engine.Runner(opts, p).run().jobs[0]
    assert (job.prompt, job.negative) == ("A fox in a field at dawn.", "blurry, watermark")


def test_api_token_from_settings(data_dir, monkeypatch):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(os.path.dirname(data_dir), "comfy.settings.json"), "w") as f:
        json.dump({engine.TOKEN_SETTING: " abc "}, f)
    assert engine.api_token() == "abc"
    monkeypatch.setenv("LMSTUDIO_API_TOKEN", "env")
    assert engine.api_token() == "env"
