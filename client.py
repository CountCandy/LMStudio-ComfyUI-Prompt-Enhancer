"""Talks to LM Studio's local server. Standard library only.

Two ways to generate:
  * OpenAI-compatible  POST /v1/chat/completions  (main path, also works with vLLM, llama.cpp)
  * Native REST        POST /api/v1/chat          (optional: adds min_p and speed stats)

The client never loads or unloads models. It only reads the model list and sends chat requests.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:1234"

API_OPENAI = "OpenAI-compatible"
API_NATIVE = "Native"
API_AUTO = "Auto (native, fall back)"
API_MODES = (API_OPENAI, API_NATIVE, API_AUTO)

# Fields a server may reject on an older version. If an HTTP 400 names one of them, the
# request is sent once more without it, and the info line says what was dropped.
DROPPABLE = (
    "reasoning_effort", "reasoning", "min_p", "top_k", "repeat_penalty", "seed", "stop",
    "response_format", "stream_options", "max_output_tokens", "store",
)

THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL | re.IGNORECASE)


class LMStudioError(Exception):
    """Base error. The message is written for the person using the node."""


class ServerUnreachable(LMStudioError):
    pass


class ModelNotLoaded(LMStudioError):
    pass


class RequestRejected(LMStudioError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"LM Studio rejected the request (HTTP {status}): {_short(body)}")


class Timeout(LMStudioError):
    pass


class Cancelled(LMStudioError):
    pass


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class ModelInfo:
    id: str
    loaded: bool | None = None  # None: the server didn't say
    vision: bool | None = None
    type: str | None = None
    display_name: str | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "loaded": self.loaded, "vision": self.vision, "type": self.type,
                "display_name": self.display_name}


@dataclass
class ChatRequest:
    model: str
    system: str
    user_text: str
    images: list[str] = field(default_factory=list)  # data URLs
    image_labels: list[str] = field(default_factory=list)  # "[image 1: 1024x768]" for previews
    params: dict[str, Any] = field(default_factory=dict)  # only values the node overrides
    reasoning: str | None = None  # None: send nothing. "off" | "low" | "medium" | "high"
    response_format: dict | None = None  # a JSON schema
    extra: dict[str, Any] = field(default_factory=dict)  # merged into the body last


@dataclass
class Reply:
    text: str
    thinking: str = ""
    finish_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_per_second: float | None = None
    seconds: float = 0.0
    endpoint: str = ""
    model: str = ""
    body: dict = field(default_factory=dict)  # the request actually sent (last attempt)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    speed_estimated: bool = False

    @property
    def cut_off(self) -> bool:
        return (self.finish_reason or "").lower() in ("length", "max_tokens", "maxpredictedtokensreached",
                                                    "max_output_tokens")


# ----------------------------------------------------------------------------------------------
# HTTP


class _Call:
    """One HTTP request on its own connection, so it can be closed from another thread."""

    def __init__(self, base_url: str, timeout: float, token: str | None = None):
        parts = urlsplit(base_url.strip() or DEFAULT_URL)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise LMStudioError(f"Server URL must look like {DEFAULT_URL}, got {base_url!r}.")
        self.base_url = f"{parts.scheme}://{parts.netloc}"
        self.prefix = parts.path.rstrip("/")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.timeout = timeout
        self.token = token
        self.conn: http.client.HTTPConnection | None = None
        self.closed = False

    def open(self, method: str, path: str, body: dict | None = None):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        self.conn = cls(self.host, self.port, timeout=self.timeout)
        headers = {"Accept": "application/json, text/event-stream"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            self.conn.request(method, self.prefix + path, body=data, headers=headers)
            return self.conn.getresponse()
        except (ConnectionError, socket.gaierror, OSError) as e:
            if self.closed:
                raise Cancelled("Cancelled.") from e
            if isinstance(e, (socket.timeout, TimeoutError)):
                raise Timeout(f"LM Studio didn't answer within {self.timeout:g} s.") from e
            raise ServerUnreachable(f"LM Studio isn't answering at {self.base_url}.") from e

    def close(self):
        self.closed = True
        conn = self.conn
        if conn is not None:
            try:
                if conn.sock is not None:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass


def _read_json(resp, call: "_Call | None" = None) -> Any:
    raw = resp.read().decode("utf-8", "replace")
    if resp.status >= 400:
        raise RequestRejected(resp.status, raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LMStudioError(f"LM Studio sent something that isn't JSON: {_short(raw)}") from e


def get_json(base_url: str, path: str, timeout: float = 10.0, token: str | None = None) -> Any:
    call = _Call(base_url, timeout, token)
    try:
        return _read_json(call.open("GET", path))
    finally:
        call.close()


# ----------------------------------------------------------------------------------------------
# Models


def _parse_models(data: Any) -> list[ModelInfo]:
    items = []
    if isinstance(data, dict):
        items = data.get("models") or data.get("data") or []
    elif isinstance(data, list):
        items = data
    out: list[ModelInfo] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = m.get("key") or m.get("id") or m.get("model_key") or m.get("path")
        if not mid:
            continue
        mtype = m.get("type")
        if isinstance(mtype, str) and "embed" in mtype.lower():
            continue
        loaded = None
        if "loaded_instances" in m:
            loaded = bool(m.get("loaded_instances"))
        elif "state" in m:
            loaded = str(m.get("state")).lower() == "loaded"
        elif "loaded" in m:
            loaded = bool(m.get("loaded"))
        caps = m.get("capabilities")
        vision = None
        if isinstance(caps, dict):
            vision = bool(caps.get("vision")) if "vision" in caps else None
        elif isinstance(caps, list):
            vision = "vision" in [str(c).lower() for c in caps]
        if vision is None and isinstance(mtype, str):
            vision = True if mtype.lower() == "vlm" else (False if mtype.lower() == "llm" and caps is not None else None)
        out.append(ModelInfo(str(mid), loaded, vision, mtype, m.get("display_name")))
        # The native list keys a model by its key; loaded copies may carry their own instance ids.
        for inst in m.get("loaded_instances") or []:
            iid = inst.get("id") if isinstance(inst, dict) else None
            if iid and iid != mid:
                out.append(ModelInfo(str(iid), True, vision, mtype, m.get("display_name")))
    return out


def list_models(base_url: str, timeout: float = 10.0, token: str | None = None) -> tuple[list[ModelInfo], str]:
    """Models LM Studio reports, and the endpoint that answered.

    Tries the native list first (it says which models are loaded), then the older native v0
    list, then the OpenAI-compatible list."""
    last_error: Exception | None = None
    for path in ("/api/v1/models", "/api/v0/models", "/v1/models"):
        try:
            data = get_json(base_url, path, timeout, token)
        except RequestRejected as e:
            last_error = e
            continue
        models = _parse_models(data)
        if models or path == "/v1/models":
            return models, path
    if last_error:
        raise last_error
    return [], ""


def find_model(models: list[ModelInfo], name: str) -> ModelInfo | None:
    for m in models:
        if m.id == name:
            return m
    low = name.lower()
    for m in models:
        if m.id.lower() == low:
            return m
    return None


def check_model(base_url: str, model: str, timeout: float = 10.0, token: str | None = None) -> ModelInfo | None:
    """Raises ModelNotLoaded when LM Studio says the model isn't loaded. Returns its info, or
    None when the server doesn't report load state (other servers)."""
    models, _ = list_models(base_url, timeout, token)
    info = find_model(models, model)
    known_state = any(m.loaded is not None for m in models)
    if info is None:
        if known_state or models:
            raise ModelNotLoaded(
                f"{model} isn't in LM Studio's model list. Pick a model from the dropdown "
                f"(refresh it), or load {model} in LM Studio.")
        return None
    if info.loaded is False:
        raise ModelNotLoaded(f"{model} isn't loaded in LM Studio. Load it in LM Studio and run again "
                             f"(the node never loads models itself).")
    return info


# ----------------------------------------------------------------------------------------------
# Request bodies


def openai_body(req: ChatRequest, stream: bool) -> dict:
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    if req.images:
        content: Any = []
        if req.user_text:
            content.append({"type": "text", "text": req.user_text})
        content += [{"type": "image_url", "image_url": {"url": url}} for url in req.images]
    else:
        content = req.user_text
    messages.append({"role": "user", "content": content})
    body: dict[str, Any] = {"model": req.model, "messages": messages}
    p = req.params
    for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens", "seed"):
        if key in p:
            body[key] = p[key]
    if p.get("stop"):
        body["stop"] = list(p["stop"])
    if req.reasoning is not None:
        body["reasoning_effort"] = "none" if req.reasoning == "off" else req.reasoning
    if req.response_format is not None:
        body["response_format"] = _json_schema_format(req.response_format)
    body["stream"] = bool(stream)
    if stream:
        body["stream_options"] = {"include_usage": True}
    body.update(req.extra)
    return body


def native_body(req: ChatRequest) -> dict:
    if req.images:
        content: Any = []
        if req.user_text:
            content.append({"type": "text", "content": req.user_text})
        content += [{"type": "image", "data_url": url} for url in req.images]
    else:
        content = req.user_text
    body: dict[str, Any] = {"model": req.model, "input": content}
    if req.system:
        body["system_prompt"] = req.system
    p = req.params
    for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "seed"):
        if key in p:
            body[key] = p[key]
    if "max_tokens" in p:
        body["max_output_tokens"] = p["max_tokens"]
    if p.get("stop"):
        body["stop"] = list(p["stop"])
    if req.reasoning is not None:
        body["reasoning"] = req.reasoning
    if req.response_format is not None:
        body["response_format"] = _json_schema_format(req.response_format)
    body["store"] = False  # every run is a fresh request; nothing carries over
    body["stream"] = False
    body.update(req.extra)
    return body


def _json_schema_format(schema: dict) -> dict:
    if (schema.get("type") == "json_schema" and "json_schema" in schema) or schema.get("type") == "json_object":
        return schema  # already a full response_format
    return {"type": "json_schema", "json_schema": {"name": schema.get("title", "prompt"), "strict": True, "schema": schema}}


def preview_body(body: dict, labels: list[str]) -> str:
    """The request as readable JSON, images shown as [image 1: 1024x1024] instead of raw data."""
    counter = iter(labels)

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and v.startswith("data:") and ";base64," in v[:80]:
            return next(counter, "[media]")
        return v

    return json.dumps(walk(body), indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------------------------------
# Replies


def split_think(text: str) -> tuple[str, str]:
    """Moves <think>…</think> blocks out of the text."""
    if "<think>" not in text.lower():
        if "</think>" in text.lower():  # template opened the block in the prompt
            head, _, tail = re.split(r"(</think>)", text, maxsplit=1, flags=re.IGNORECASE)
            return tail.strip(), head.strip()
        return text, ""
    thoughts = [m.group(1).strip() for m in THINK_RE.finditer(text)]
    return THINK_RE.sub("", text).strip(), "\n\n".join(t for t in thoughts if t)


def parse_openai(data: dict) -> Reply:
    choices = data.get("choices") or []
    if not choices:
        raise LMStudioError(f"LM Studio's reply has no choices: {_short(json.dumps(data))}")
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    thinking = msg.get("reasoning_content") or msg.get("reasoning") or ""
    text, inline = split_think(text)
    usage = data.get("usage") or {}
    stats = data.get("stats") or {}
    return Reply(
        text=text, thinking=(thinking or inline).strip(), finish_reason=choices[0].get("finish_reason"),
        tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
        tokens_per_second=stats.get("tokens_per_second"),
    )


def parse_native(data: dict) -> Reply:
    if "choices" in data:
        return parse_openai(data)
    texts, thoughts = [], []
    output = data.get("output")
    if isinstance(output, str):
        texts.append(output)
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "message")).lower()
        content = item.get("content")
        if isinstance(content, list):
            content = "".join(c.get("text", c.get("content", "")) for c in content if isinstance(c, dict))
        (thoughts if "reason" in kind or "think" in kind else texts).append(str(content or ""))
    stats = data.get("stats") or {}
    text, inline = split_think("".join(texts))
    reasoning_tokens = stats.get("reasoning_output_tokens")
    finish = stats.get("stop_reason") or data.get("stop_reason") or data.get("finish_reason")
    reply = Reply(
        text=text, thinking=("\n\n".join(t for t in thoughts if t) or inline).strip(), finish_reason=finish,
        tokens_in=stats.get("input_tokens"),
        tokens_out=stats.get("total_output_tokens") or stats.get("output_tokens"),
        tokens_per_second=stats.get("tokens_per_second"),
    )
    if reasoning_tokens:
        reply.notes.append(f"{reasoning_tokens} thinking tokens")
    return reply


# ----------------------------------------------------------------------------------------------
# Generate


ShouldCancel = Callable[[], bool]
OnText = Callable[[str, str], None]


class Client:
    def __init__(self, base_url: str = DEFAULT_URL, api_mode: str = API_OPENAI, timeout: float = 300.0,
                 token: str | None = None, stream: bool = True):
        self.base_url = (base_url or DEFAULT_URL).strip().rstrip("/")
        self.api_mode = api_mode if api_mode in API_MODES else API_OPENAI
        self.timeout = float(timeout)
        self.token = token or None
        self.stream = stream

    # -- public -------------------------------------------------------------------------------

    def chat(self, req: ChatRequest, should_cancel: ShouldCancel | None = None,
             on_text: OnText | None = None) -> Reply:
        started = time.monotonic()
        if self.api_mode == API_OPENAI:
            reply = self._chat_openai(req, should_cancel, on_text)
        elif self.api_mode == API_NATIVE:
            reply = self._chat_native(req, should_cancel)
        else:
            try:
                reply = self._chat_native(req, should_cancel)
            except RequestRejected as e:
                if e.status not in (404, 405, 501):
                    raise
                reply = self._chat_openai(req, should_cancel, on_text)
                reply.notes.append("native endpoint missing, used OpenAI-compatible")
        reply.seconds = time.monotonic() - started
        reply.model = req.model
        if reply.tokens_per_second is None and reply.tokens_out and reply.seconds > 0:
            reply.tokens_per_second = reply.tokens_out / reply.seconds  # includes reading the prompt
            reply.speed_estimated = True
        return reply

    # -- endpoints -----------------------------------------------------------------------------

    def _chat_openai(self, req, should_cancel, on_text) -> Reply:
        stream = self.stream and on_text is not None
        body = openai_body(req, stream)
        if "min_p" in body:
            body.pop("min_p")
            note = "min_p skipped (native endpoint only)"
        else:
            note = None
        reply = self._send("/v1/chat/completions", body, should_cancel,
                           (lambda resp, call: self._read_sse(resp, on_text, call)) if stream else None,
                           parse_openai)
        reply.endpoint = "/v1/chat/completions"
        if note:
            reply.notes.append(note)
        return reply

    def _chat_native(self, req, should_cancel) -> Reply:
        reply = self._send("/api/v1/chat", native_body(req), should_cancel, None, parse_native)
        reply.endpoint = "/api/v1/chat"
        return reply

    def _send(self, path, body, should_cancel, stream_reader, parser) -> Reply:
        dropped: list[str] = []
        for _ in range(4):
            try:
                if stream_reader is not None and body.get("stream"):
                    reply = self._run(path, body, should_cancel, stream_reader)
                else:
                    data = self._run(path, body, should_cancel, _read_json)
                    reply = parser(data)
            except RequestRejected as e:
                if e.status != 400:
                    raise
                bad = [k for k in DROPPABLE if k in body and re.search(rf"\b{re.escape(k)}\b", e.body)]
                if not bad:
                    raise
                for k in bad:
                    body.pop(k, None)
                    if k == "stream_options":
                        continue
                    dropped.append(k)
                continue
            reply.body = body
            reply.dropped = dropped
            if dropped:
                reply.notes.append("server ignored " + ", ".join(dropped))
            return reply
        raise LMStudioError("LM Studio kept rejecting the request; see the console for details.")

    def _run(self, path, body, should_cancel, reader):
        """Runs one request on a worker thread so ComfyUI's Cancel can stop it at once."""
        call = _Call(self.base_url, self.timeout, self.token)
        box: dict[str, Any] = {}

        def work():
            try:
                resp = call.open("POST", path, body)
                if resp.status >= 400:
                    raise RequestRejected(resp.status, resp.read().decode("utf-8", "replace"))
                box["value"] = reader(resp, call)
            except BaseException as e:  # handed to the caller
                box["error"] = e

        worker = threading.Thread(target=work, name="lmstudio-request", daemon=True)
        worker.start()
        deadline = time.monotonic() + self.timeout
        try:
            while worker.is_alive():
                worker.join(0.1)
                if should_cancel is not None and should_cancel():
                    call.close()
                    raise Cancelled("Cancelled.")
                if time.monotonic() > deadline:
                    call.close()
                    raise Timeout(f"LM Studio didn't finish within {self.timeout:g} s (timeout setting).")
        finally:
            if not worker.is_alive():
                call.close()
        if "error" in box:
            err = box["error"]
            if isinstance(err, (socket.timeout, TimeoutError)):
                raise Timeout(f"LM Studio didn't answer within {self.timeout:g} s.") from err
            if isinstance(err, (ConnectionError, OSError)) and not isinstance(err, LMStudioError):
                raise ServerUnreachable(f"LM Studio stopped answering at {self.base_url}.") from err
            raise err
        return box.get("value")

    @staticmethod
    def _read_sse(resp, on_text: OnText | None, call: "_Call") -> Reply:
        text, thinking = [], []
        finish = None
        usage: dict = {}
        stats: dict = {}
        last_push = 0.0
        for raw in resp:
            # Cancelled or timed out: text already received must not reach the node any more.
            if call.closed:
                raise Cancelled("Cancelled.")
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise LMStudioError(f"LM Studio stopped with an error: {_short(json.dumps(chunk['error']))}")
            usage = chunk.get("usage") or usage
            stats = chunk.get("stats") or stats
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                r = delta.get("reasoning_content") or delta.get("reasoning")
                if r:
                    thinking.append(r)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            now = time.monotonic()
            if on_text is not None and now - last_push > 0.15:
                last_push = now
                shown, inline = split_think("".join(text))
                on_text(shown, "".join(thinking) or inline)
        full, inline = split_think("".join(text))
        if on_text is not None:
            on_text(full, "".join(thinking) or inline)
        return Reply(text=full, thinking=("".join(thinking) or inline).strip(), finish_reason=finish,
                     tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                     tokens_per_second=stats.get("tokens_per_second"))


def test_connection(base_url: str, model: str, timeout: float = 10.0, token: str | None = None) -> dict:
    """What the Test button shows: reachable, model loaded, accepts images."""
    result = {"ok": False, "reachable": False, "loaded": None, "vision": None, "message": "", "models": []}
    try:
        models, endpoint = list_models(base_url, timeout, token)
    except LMStudioError as e:
        result["message"] = str(e)
        return result
    result["reachable"] = True
    result["models"] = [m.as_dict() for m in models]
    if not model:
        result["message"] = f"LM Studio answered ({endpoint}). Pick a model."
        return result
    info = find_model(models, model)
    if info is None:
        result["message"] = f"LM Studio answered, but {model} isn't in its list."
        return result
    result["loaded"] = info.loaded
    result["vision"] = info.vision
    if info.loaded is False:
        result["message"] = f"{model} isn't loaded in LM Studio."
        return result
    result["ok"] = True
    pictures = {True: "accepts images", False: "text only", None: "image support unknown"}[info.vision]
    state = "loaded" if info.loaded else "listed"
    result["message"] = f"{model} is {state} · {pictures}"
    return result
