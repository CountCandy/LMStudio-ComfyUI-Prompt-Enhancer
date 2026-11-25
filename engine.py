"""One run of the node, without ComfyUI: builds requests, calls LM Studio, checks and logs replies."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import client as lm
from . import media
from . import presets as presets_mod
from . import prompt_tools as pt

log = logging.getLogger("LMStudioPromptEnhancer")

ON_ERROR_STOP = "Stop"
ON_ERROR_PASS = "Pass through"
REASONING = ("off", "low", "medium", "high", "model default")
IMAGE_BATCH = ("first", "all")

# Where the panel's model list and Test button look when they aren't told: the last URL a run used.
STATE = {"server_url": lm.DEFAULT_URL, "api_mode": lm.API_OPENAI}
_history_lock = threading.Lock()


class UserError(Exception):
    """A problem the person can fix; the message says how."""


@dataclass
class JobResult:
    prompt: str
    negative: str = ""
    thinking: str = ""
    request: str = ""
    info: str = ""
    warnings: list[str] = field(default_factory=list)
    seed: int | None = None
    idea: str = ""


@dataclass
class RunResult:
    jobs: list[JobResult]
    passed_through: bool = False

    def column(self, name: str) -> list[str]:
        return [getattr(j, name) for j in self.jobs]

    @property
    def warnings(self) -> list[str]:
        out = []
        for j in self.jobs:
            out += [w for w in j.warnings if w not in out]
        return out


DEFAULTS: dict[str, Any] = {
    "words": "", "model": "", "length": "long", "trigger_words": "", "slots": "",
    "use_temperature": True, "temperature": 0.7,
    "use_max_tokens": True, "max_tokens": 400,
    "use_seed": True, "seed": 0,
    "reasoning": "off", "thinking_budget": 1024,
    "use_top_p": False, "top_p": 0.95, "use_top_k": False, "top_k": 40,
    "use_min_p": False, "min_p": 0.05, "use_repeat_penalty": False, "repeat_penalty": 1.1,
    "use_stop": False, "stop": "", "use_response_format": False, "response_format": "",
    "extra_json": "", "checks": True, "variations": 1, "one_per_line": False,
    "token_limit": 512, "history": True, "on_error": ON_ERROR_STOP,
    "server_url": lm.DEFAULT_URL, "api_mode": lm.API_OPENAI, "timeout": 300,
    "max_image_side": media.DEFAULT_MAX_SIDE, "image_batch": "first", "stream": True, "debug": False,
    "instruction_override": None,
    "idea": None,  # text from the idea socket; when connected it replaces the words box
}

MODE_LABELS = {"words": "words → text", "picture": "picture → text"}


TOKEN_SETTING = "LMStudioPromptEnhancer.ApiToken"


def api_token() -> str | None:
    """The LM Studio API token: from ComfyUI's settings (never the workflow), or LMSTUDIO_API_TOKEN."""
    env = os.environ.get("LMSTUDIO_API_TOKEN")
    if env:
        return env.strip()
    path = os.path.join(os.path.dirname(presets_mod.data_dir()), "comfy.settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f).get(TOKEN_SETTING)
    except (OSError, ValueError, AttributeError):
        return None
    return str(value).strip() or None if value else None


def options(**kwargs) -> dict[str, Any]:
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in kwargs.items() if k in DEFAULTS})
    return opts


def sampling_params(opts: dict) -> dict[str, Any]:
    """Only the parameters whose tick is on: everything else is left to LM Studio's model settings."""
    p: dict[str, Any] = {}
    if opts["use_temperature"]:
        p["temperature"] = float(opts["temperature"])
    if opts["use_top_p"]:
        p["top_p"] = float(opts["top_p"])
    if opts["use_top_k"]:
        p["top_k"] = int(opts["top_k"])
    if opts["use_min_p"]:
        p["min_p"] = float(opts["min_p"])
    if opts["use_repeat_penalty"]:
        p["repeat_penalty"] = float(opts["repeat_penalty"])
    if opts["use_max_tokens"]:
        tokens = int(opts["max_tokens"])
        if opts["reasoning"] in ("low", "medium", "high"):
            tokens += int(opts["thinking_budget"])  # thinking gets its own room
        p["max_tokens"] = tokens
    if opts["use_stop"]:
        stops = [s for s in str(opts["stop"]).splitlines() if s]
        if stops:
            p["stop"] = stops
    return p


def _json_field(text: str, label: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise UserError(f"{label} isn't valid JSON: {e}") from e


def pick_mode(preset: presets_mod.Preset, has_image: bool) -> str:
    """words to text unless an image arrives. A bypassed or muted image node sends no image,
    so it switches back to words by itself."""
    if preset.mode in ("auto", "both"):
        return "picture" if has_image else "words"
    return preset.mode


def build_request(opts: dict, preset: presets_mod.Preset, idea: str, mode: str,
                  images: list[media.EncodedImage], params: dict, seed: int | None) -> lm.ChatRequest:
    values = dict(preset.slots)
    values.update({k: v for k, v in pt.parse_slot_lines(opts["slots"]).items() if v or k not in values})
    values["words"] = idea.strip()
    values["length"] = pt.length_text(opts["length"], preset.lengths)
    values["trigger"] = str(opts["trigger_words"] or "").strip()
    instruction = opts.get("instruction_override")
    system = pt.fill(instruction if instruction and instruction.strip() else preset.instruction_for(mode), values)
    user_text = pt.fill(preset.template_for(mode), values)
    response_format = preset.response_format
    if opts["use_response_format"]:
        response_format = _json_field(opts["response_format"], "response_format")
    extra = _json_field(opts["extra_json"], "extra_json") or {}
    if not isinstance(extra, dict):
        raise UserError("extra_json must be a JSON object, like {\"top_k\": 20}.")
    p = dict(params)
    if seed is not None:
        p["seed"] = int(seed)
    reasoning = None if opts["reasoning"] == "model default" else opts["reasoning"]
    return lm.ChatRequest(
        model=opts["model"], system=system, user_text=user_text,
        images=[i.data_url for i in images] if mode == "picture" else [],
        image_labels=[i.label for i in images] if mode == "picture" else [],
        params=p, reasoning=reasoning, response_format=response_format, extra=extra,
    )


def word_range(opts: dict, preset: presets_mod.Preset) -> tuple[int, int] | None:
    words = preset.checks.get("words")
    if isinstance(words, (list, tuple)) and len(words) == 2:
        return int(words[0]), int(words[1])
    if preset.uses("length"):
        return pt.LENGTHS.get(opts["length"], ("", None))[1]
    return None


def info_line(reply: lm.Reply, extra: list[str]) -> str:
    parts = [reply.model, reply.endpoint]
    if reply.tokens_in is not None or reply.tokens_out is not None:
        parts.append(f"{reply.tokens_in if reply.tokens_in is not None else '?'} in / "
                     f"{reply.tokens_out if reply.tokens_out is not None else '?'} out")
    if reply.tokens_per_second:
        parts.append(f"{reply.tokens_per_second:.1f} tok/s")
    parts.append(f"{reply.seconds:.1f} s")
    if reply.cut_off:
        parts.append("hit the token limit")
    return " · ".join(p for p in parts + reply.notes + extra if p)


class Runner:
    def __init__(self, opts: dict, preset: presets_mod.Preset, image=None, clip=None,
                 should_cancel: Callable[[], bool] | None = None,
                 on_stream: Callable[[str, str], None] | None = None, token: str | None = None):
        self.opts = opts
        self.preset = preset
        self.image = image
        self.clip = clip
        self.should_cancel = should_cancel
        self.on_stream = on_stream
        self.client = lm.Client(opts["server_url"], opts["api_mode"], opts["timeout"], token, opts["stream"])

    # -- entry --------------------------------------------------------------------------------

    def run(self) -> RunResult:
        try:
            return self._run()
        except (lm.Cancelled, KeyboardInterrupt):
            raise
        except (UserError, lm.LMStudioError) as e:
            if self.opts["on_error"] != ON_ERROR_PASS:
                raise
            log.warning("LM Studio Prompt Enhancer: %s (passing your words through)", e)
            ideas = self._ideas()
            return RunResult([JobResult(prompt=i.strip(), info=f"passed through: {e}", warnings=[f"Passed through: {e}"],
                                        idea=i) for i in ideas], passed_through=True)

    @property
    def idea_connected(self) -> bool:
        return self.opts.get("idea") is not None

    def _ideas(self) -> list[str]:
        source = self.opts["idea"] if self.idea_connected else self.opts["words"]
        words = str(source or "")
        if self.opts["one_per_line"]:
            ideas = [line for line in words.splitlines() if line.strip()]
            return ideas or [""]
        return [words]

    def _run(self) -> RunResult:
        opts, preset = self.opts, self.preset
        if not str(opts["model"] or "").strip():
            raise UserError("Pick a model: the dropdown lists the models LM Studio reports.")
        STATE["server_url"] = opts["server_url"]
        STATE["api_mode"] = opts["api_mode"]
        mode = self.mode = pick_mode(preset, self.image is not None)
        ideas = self._ideas()
        if mode == "picture" and self.image is None:
            raise UserError(f"The preset {preset.name!r} works from a picture: connect an image (and make sure its node isn't bypassed).")
        if mode == "words" and not any(i.strip() for i in ideas):
            if self.idea_connected:
                raise UserError("The text coming into the idea socket is empty.")
            raise UserError("Type your idea in the words box, or connect text to the idea socket.")

        images = media.encode_images(self.image, opts["image_batch"], int(opts["max_image_side"])) if mode == "picture" else []
        info = lm.check_model(self.client.base_url, opts["model"], min(10.0, float(opts["timeout"])), self.client.token)
        warnings_all = []
        if mode == "picture" and info is not None and info.vision is False:
            warnings_all.append(f"{opts['model']} may not accept images (LM Studio says it has no vision).")

        params = sampling_params(opts)
        variations = max(1, int(opts["variations"]))
        jobs = []
        base_seed = int(opts["seed"]) if opts["use_seed"] else None
        for i, idea in enumerate(ideas):
            for v in range(variations):
                k = i * variations + v
                seed = None if base_seed is None else (base_seed + k) % (2**63)
                jobs.append((idea, seed))

        single = len(jobs) == 1
        if single:
            results = [self._job(*jobs[0], mode=mode, images=images, params=params, stream=True)]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="lmstudio-job") as pool:
                futures = [pool.submit(self._job, idea, seed, mode=mode, images=images, params=params, stream=False)
                           for idea, seed in jobs]
                results = [f.result() for f in futures]
        for r in results:
            r.warnings = warnings_all + r.warnings
        if opts["history"]:
            self._history(results)
        return RunResult(results)

    # -- one prompt ---------------------------------------------------------------------------

    def _job(self, idea: str, seed: int | None, mode: str, images, params, stream: bool) -> JobResult:
        opts, preset = self.opts, self.preset
        rng = random.Random(seed if seed is not None else time.time_ns())
        resolved = pt.resolve_choices(idea, rng)
        structured = bool(preset.response_format) or bool(opts["use_response_format"] and str(opts["response_format"]).strip())
        protected = pt.protected_texts(resolved, opts["trigger_words"])
        checks_on = bool(opts["checks"]) and not structured
        attempts = 2 if checks_on else 1
        best: tuple[int, JobResult] | None = None
        for attempt in range(attempts):
            attempt_seed = seed if attempt == 0 or seed is None else seed + 1_000_003
            req = build_request(opts, preset, resolved, mode, images, params, attempt_seed)
            reply = self.client.chat(req, self.should_cancel, self.on_stream if stream else None)
            result = self._finish(reply, req, preset, structured, protected, attempt_seed, resolved)
            if not checks_on:
                return result
            check = pt.check_prompt(result.prompt, protected, word_range(opts, preset),
                                    bool(preset.checks.get("no_negation", True)),
                                    bool(preset.checks.get("no_labels", True)))
            result.warnings += [f"Check: {p}" for p in check.problems]
            if best is None or len(check.problems) < best[0]:
                best = (len(check.problems), result)
            if check.ok:
                break
            log.info("LM Studio Prompt Enhancer: check failed (%s)%s", "; ".join(check.problems),
                     ", retrying with a new seed" if attempt == 0 else "")
        final = best[1]
        if attempt == 1:
            final.info += " · retried after a failed check"
        return final

    def _finish(self, reply: lm.Reply, req: lm.ChatRequest, preset, structured: bool, protected, seed, idea) -> JobResult:
        text = pt.clean_reply(reply.text, structured)
        preview = lm.preview_body(reply.body, req.image_labels)
        if not text:
            why = "it hit the token limit" if reply.cut_off else "the reply was empty"
            hint = (" Thinking may have used the room: raise thinking_budget or turn thinking off."
                    if reply.thinking else " Raise max_tokens." if reply.cut_off else "")
            raise lm.LMStudioError(f"LM Studio sent no prompt ({why}).{hint} [{info_line(reply, [])}]")
        if reply.cut_off and not structured:
            raise lm.LMStudioError(f"The prompt was cut off: it hit the token limit. Raise max_tokens. "
                                   f"[{info_line(reply, [])}]")
        negative = ""
        if preset.negative and not structured:
            text, negative = pt.split_negative(text, preset.negative_marker)
        lead = [MODE_LABELS.get(getattr(self, "mode", ""), "")] + (["idea from socket"] if self.idea_connected else [])
        extra, warnings = [], []
        tokens = pt.count_tokens(self.clip, text) if self.clip is not None else None
        if tokens is not None:
            extra.append(f"{tokens} encoder tokens")
            if tokens > int(self.opts["token_limit"]):
                warnings.append(f"{tokens} tokens in the text encoder, over the limit of {self.opts['token_limit']}.")
        if structured:
            try:
                json.loads(text)
            except json.JSONDecodeError:
                warnings.append("The reply isn't valid JSON.")
        info = " · ".join([p for p in lead if p] + [info_line(reply, extra)])
        log.info("LM Studio Prompt Enhancer: %s", info)
        if self.opts["debug"]:
            log.info("LM Studio Prompt Enhancer request:\n%s\nreply:\n%s\nthinking:\n%s", preview, reply.text, reply.thinking)
        return JobResult(prompt=text, negative=negative, thinking=reply.thinking, request=preview, info=info,
                         warnings=warnings, seed=seed, idea=idea)

    # -- history ------------------------------------------------------------------------------

    def _history(self, results: list[JobResult]) -> None:
        path = os.path.join(presets_mod.data_dir(), "history.jsonl")
        keep = ("model", "length", "trigger_words", "slots", "reasoning", "server_url", "api_mode")
        settings = {k: self.opts[k] for k in keep}
        settings["params"] = sampling_params(self.opts)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with _history_lock, open(path, "a", encoding="utf-8") as f:
                for r in results:
                    f.write(json.dumps({
                        "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "preset": self.preset.name,
                        "words": r.idea, "seed": r.seed, "settings": settings, "prompt": r.prompt,
                        "negative": r.negative, "info": r.info, "warnings": r.warnings,
                        "picture": self.image is not None,
                    }, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("LM Studio Prompt Enhancer: couldn't write history (%s)", e)
