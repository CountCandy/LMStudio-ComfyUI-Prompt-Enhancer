import json
import threading
import time

import pytest

from lmse import client as lm


def req(**kw):
    base = dict(model="google/gemma-4-31b", system="Be brief.", user_text="a cat")
    base.update(kw)
    return lm.ChatRequest(**base)


def test_list_models_native_marks_loaded_and_vision(fake):
    models, endpoint = lm.list_models(fake.url)
    assert endpoint == "/api/v1/models"
    by_id = {m.id: m for m in models}
    assert by_id["google/gemma-4-31b"].loaded is True
    assert by_id["google/gemma-4-31b"].vision is True
    assert by_id["qwen/qwen3-8b"].loaded is False
    assert "text-embedding-nomic" not in by_id  # embeddings can't write prompts


def test_list_models_falls_back_to_openai_list(fake):
    fake.native = False
    models, endpoint = lm.list_models(fake.url)
    assert endpoint == "/v1/models"
    assert all(m.loaded is None for m in models)


def test_parse_v0_models():
    models = lm._parse_models({"data": [{"id": "a", "type": "vlm", "state": "loaded"},
                                        {"id": "b", "type": "llm", "state": "not-loaded"}]})
    assert [(m.id, m.loaded, m.vision) for m in models] == [("a", True, True), ("b", False, None)]


def test_unreachable_server_message():
    with pytest.raises(lm.ServerUnreachable, match="isn't answering at http://127.0.0.1:9"):
        lm.list_models("http://127.0.0.1:9", timeout=2)


def test_check_model_not_loaded(fake):
    with pytest.raises(lm.ModelNotLoaded, match="qwen/qwen3-8b isn't loaded"):
        lm.check_model(fake.url, "qwen/qwen3-8b")
    with pytest.raises(lm.ModelNotLoaded, match="isn't in LM Studio's model list"):
        lm.check_model(fake.url, "nope/missing")
    assert lm.check_model(fake.url, "google/gemma-4-31b").loaded


def test_openai_body_only_sends_ticked_params_and_images():
    r = req(params={"temperature": 0.7, "max_tokens": 400}, images=["data:image/png;base64,AAAA"], reasoning="off")
    body = lm.openai_body(r, stream=False)
    assert body["temperature"] == 0.7 and body["max_tokens"] == 400
    assert "top_p" not in body and "seed" not in body
    assert body["reasoning_effort"] == "none"
    content = body["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "a cat"}
    assert content[1]["type"] == "image_url"
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}


def test_native_body_fields():
    r = req(params={"max_tokens": 50, "min_p": 0.05}, images=["data:image/png;base64,AAAA"], reasoning="low")
    body = lm.native_body(r)
    assert body["max_output_tokens"] == 50 and body["min_p"] == 0.05
    assert body["system_prompt"] == "Be brief." and body["reasoning"] == "low"
    assert body["input"][1] == {"type": "image", "data_url": "data:image/png;base64,AAAA"}
    assert body["store"] is False


def test_chat_openai_plain(fake):
    reply = lm.Client(fake.url, stream=False).chat(req(params={"temperature": 0.5}))
    assert reply.text.startswith("A cinematic photograph")
    assert reply.endpoint == "/v1/chat/completions"
    assert reply.tokens_in == 120 and reply.tokens_out > 10
    path, body = fake.requests[-1]
    assert body["temperature"] == 0.5 and body["stream"] is False


def test_chat_openai_streaming_and_thinking(fake):
    fake.thinking = "Let me think."
    seen = []
    reply = lm.Client(fake.url, stream=True).chat(req(), on_text=lambda t, th: seen.append((t, th)))
    assert reply.text.startswith("A cinematic photograph") and reply.thinking == "Let me think."
    assert seen and seen[-1][0] == reply.text
    assert reply.tokens_out is not None


def test_chat_native_stats(fake):
    fake.thinking = "hmm"
    reply = lm.Client(fake.url, api_mode=lm.API_NATIVE).chat(req(params={"min_p": 0.1}))
    assert reply.endpoint == "/api/v1/chat"
    assert reply.tokens_per_second == 42.5 and reply.thinking == "hmm"
    assert fake.requests[-1][1]["min_p"] == 0.1


def test_auto_falls_back_when_native_missing(fake):
    fake.native = False
    reply = lm.Client(fake.url, api_mode=lm.API_AUTO, stream=False).chat(req())
    assert reply.endpoint == "/v1/chat/completions"
    assert any("native endpoint missing" in n for n in reply.notes)


def test_min_p_skipped_on_openai(fake):
    reply = lm.Client(fake.url, stream=False).chat(req(params={"min_p": 0.1}))
    assert "min_p" not in fake.requests[-1][1]
    assert any("min_p" in n for n in reply.notes)


def test_rejected_field_is_dropped_and_noted(fake):
    fake.reject_fields = {"reasoning_effort"}
    reply = lm.Client(fake.url, stream=False).chat(req(reasoning="off"))
    assert reply.dropped == ["reasoning_effort"]
    assert "reasoning_effort" not in reply.body


def test_cut_off_detected(fake):
    fake.finish_reason = "length"
    assert lm.Client(fake.url, stream=False).chat(req()).cut_off


def test_think_tags_split():
    text, thinking = lm.split_think("<think>plan it</think>\nA red fox.")
    assert text == "A red fox." and thinking == "plan it"
    assert lm.split_think("plan</think>A fox.") == ("A fox.", "plan")


def test_cancel_stops_waiting(fake):
    fake.delay = 3.0
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    started = time.monotonic()
    with pytest.raises(lm.Cancelled):
        lm.Client(fake.url, stream=False).chat(req(), should_cancel=flag.is_set)
    assert time.monotonic() - started < 2.0


def test_timeout(fake):
    fake.delay = 3.0
    with pytest.raises(lm.Timeout):
        lm.Client(fake.url, timeout=0.5, stream=False).chat(req())


def test_token_sent(fake):
    fake.token = "secret"
    with pytest.raises(lm.RequestRejected):
        lm.list_models(fake.url)
    models, _ = lm.list_models(fake.url, token="secret")
    assert models


def test_preview_hides_image_data():
    body = lm.openai_body(req(images=["data:image/png;base64," + "A" * 5000]), stream=False)
    text = lm.preview_body(body, ["[image 1: 1024x768]"])
    assert "[image 1: 1024x768]" in text and "AAAA" not in text
    json.loads(text)


def test_test_connection(fake):
    ok = lm.test_connection(fake.url, "google/gemma-4-31b")
    assert ok["ok"] and ok["vision"] is True and "accepts images" in ok["message"]
    assert not lm.test_connection(fake.url, "qwen/qwen3-8b")["ok"]
    assert not lm.test_connection("http://127.0.0.1:9", "x", timeout=2)["reachable"]


def test_no_text_after_cancel_while_streaming(fake):
    fake.delay = 1.0  # 1 s before the reply, then 0.1 s per word
    seen = []
    flag = threading.Event()
    threading.Timer(1.6, flag.set).start()
    with pytest.raises(lm.Cancelled):
        lm.Client(fake.url, stream=True).chat(req(), should_cancel=flag.is_set,
                                              on_text=lambda t, th: seen.append(time.monotonic()))
    stopped = time.monotonic()
    time.sleep(1.0)  # the reader thread would still be delivering words here
    assert seen, "some text should have streamed before the cancel"
    assert not [t for t in seen if t > stopped]
