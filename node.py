"""The LM Studio Prompt Enhancer node: inputs, outputs, run."""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import client as lm
from . import engine
from . import presets
from . import prompt_tools as pt

log = logging.getLogger("LMStudioPromptEnhancer")

NODE_ID = "LMStudioPromptEnhancer"
STREAM_EVENT = "lmse.stream"
SAVED_PROPERTY = "lmse_saved"  # where the panel keeps the last result in the workflow

# Last result per node, for hold. The panel also saves it into the workflow (and so into every
# PNG), which is read back when ComfyUI restarted since the last run.
HELD: dict[str, dict] = {}

OUTPUT_NAMES = ("prompt", "negative", "thinking", "request", "info")
DEFAULT_PRESET = "MOSS"

DESCRIPTION = (
    "Sends your words and/or a picture to a model running in LM Studio, with the saved instruction "
    "(preset) you pick, and returns the rewritten prompt. Nothing is added behind your back: the request "
    "output shows exactly what the model received. Presets are Markdown files; your own go in "
    "ComfyUI/user/default/lmstudio-prompt-enhancer/presets."
)


def _tick(name: str, what: str, default: bool, advanced: bool) -> io.Boolean.Input:
    return io.Boolean.Input(f"use_{name}", default=default, advanced=advanced,
                            tooltip=f"On: send {what}. Off: send nothing, so LM Studio's own setting for the model applies.")


class LMStudioPromptEnhancer(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        d = engine.DEFAULTS
        return io.Schema(
            node_id=NODE_ID,
            display_name="LM Studio Prompt Enhancer",
            category="LM Studio",
            description=DESCRIPTION,
            search_aliases=["prompt enhancer", "lm studio", "lmstudio", "gemma", "moss", "rewrite prompt",
                            "caption", "llm prompt", "candy"],
            inputs=[
                io.Combo.Input("preset", options=(names := presets.LIBRARY.names()),
                               default=DEFAULT_PRESET if DEFAULT_PRESET in names else names[0],
                               tooltip="The saved instruction. A preset with words and picture sections uses the words one "
                                       "by default and the picture one when an image is connected."),
                io.String.Input("words", multiline=True, default="", dynamic_prompts=False,
                                placeholder="Your idea (or connect one to the idea socket). With a picture: your changes, optional. {a|b} picks one.",
                                tooltip="Your idea in words mode; your changes in picture mode. Choices in braces "
                                        "like {red|blue} are picked by the seed. Text in \"quotes\" must come back exactly."),
                io.Combo.Input("model", options=[], remote=io.RemoteOptions(route="/lmse/models", refresh_button=True),
                               tooltip="A model LM Studio reports. The exact name is always sent. The node never loads or unloads models."),
                io.Combo.Input("length", options=list(pt.LENGTH_NAMES), default=d["length"],
                               tooltip="Fills a preset's {length} slot. MOSS was trained on captions of about 21, 77 and 235 tokens."),
                io.String.Input("trigger_words", default="", tooltip="LoRA trigger words, comma-separated. Fill a preset's "
                                "{trigger} slot and must come back letter for letter (checked)."),
                io.String.Input("slots", multiline=True, default="", advanced=True,
                                tooltip="Values for a preset's own slots, one 'name: value' per line. The panel shows these as fields."),
                _tick("temperature", "temperature", d["use_temperature"], False),
                io.Float.Input("temperature", default=d["temperature"], min=0.0, max=2.0, step=0.05, round=0.01,
                               tooltip="Lower keeps closer to your idea."),
                _tick("max_tokens", "max_tokens", d["use_max_tokens"], False),
                io.Int.Input("max_tokens", default=d["max_tokens"], min=1, max=131072,
                             tooltip="Room for the prompt: 400 is about 300 words. Thinking gets thinking_budget on top."),
                _tick("seed", "the seed", d["use_seed"], False),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFFFFFFFFFF, control_after_generate=io.ControlAfterGenerate.fixed,
                             tooltip="Fixed: the same prompt every queue. Randomize: a new prompt every queue."),
                io.Combo.Input("reasoning", options=list(engine.REASONING), default=d["reasoning"],
                               tooltip="Thinking. Off is fastest. 'model default' sends nothing."),
                io.Boolean.Input("hold", default=False,
                                 tooltip="Freeze the last prompt: no call to LM Studio, so you can tweak downstream on a prompt you like."),
                # advanced
                io.Int.Input("thinking_budget", default=d["thinking_budget"], min=0, max=131072, advanced=True,
                             tooltip="Extra tokens added to max_tokens while thinking is on, so reasoning can't eat the prompt."),
                _tick("top_p", "top_p", False, True),
                io.Float.Input("top_p", default=d["top_p"], min=0.0, max=1.0, step=0.01, advanced=True),
                _tick("top_k", "top_k", False, True),
                io.Int.Input("top_k", default=d["top_k"], min=0, max=1000, advanced=True),
                _tick("min_p", "min_p (native endpoint only)", False, True),
                io.Float.Input("min_p", default=d["min_p"], min=0.0, max=1.0, step=0.01, advanced=True),
                _tick("repeat_penalty", "repeat_penalty", False, True),
                io.Float.Input("repeat_penalty", default=d["repeat_penalty"], min=0.5, max=2.0, step=0.01, advanced=True),
                _tick("stop", "stop strings", False, True),
                io.String.Input("stop", multiline=True, default="", advanced=True, tooltip="One stop string per line."),
                _tick("response_format", "this JSON schema instead of the preset's", False, True),
                io.String.Input("response_format", multiline=True, default="", advanced=True,
                                tooltip="A JSON schema to force structured output."),
                io.String.Input("extra_json", multiline=True, default="", advanced=True,
                                tooltip="Extra fields merged into the request as JSON, e.g. {\"presence_penalty\": 0.2}."),
                io.Boolean.Input("checks", default=True, advanced=True,
                                 tooltip="Check word count, no negative phrasing, no labels, protected text; retry once with a new seed."),
                io.Int.Input("variations", default=1, min=1, max=16, advanced=True,
                             tooltip="Prompts per idea, sent in parallel with seeds seed, seed+1, …"),
                io.Boolean.Input("one_per_line", default=False, advanced=True,
                                 tooltip="Each line of words is its own idea; the outputs are lists."),
                io.Int.Input("token_limit", default=d["token_limit"], min=1, max=100000, advanced=True,
                             tooltip="With clip connected: warn when the prompt is longer than this in the encoder's tokens."),
                io.Boolean.Input("history", default=True, advanced=True,
                                 tooltip="Save each prompt with its inputs to user/default/lmstudio-prompt-enhancer/history.jsonl."),
                io.Combo.Input("on_error", options=[engine.ON_ERROR_STOP, engine.ON_ERROR_PASS], default=engine.ON_ERROR_STOP,
                               advanced=True, tooltip="Pass through: send your words on and warn, so a long batch keeps going."),
                # connection (the gear panel)
                io.String.Input("server_url", default=lm.DEFAULT_URL, advanced=True),
                io.Combo.Input("api_mode", options=list(lm.API_MODES), default=lm.API_OPENAI, advanced=True,
                               tooltip="OpenAI-compatible is documented and stable. Native adds min_p and speed stats."),
                io.Int.Input("timeout", default=d["timeout"], min=5, max=3600, advanced=True, tooltip="Seconds."),
                io.Int.Input("max_image_side", default=d["max_image_side"], min=64, max=8192, step=8, advanced=True,
                             tooltip="Pictures are scaled down so their longest side is at most this."),
                io.Combo.Input("image_batch", options=list(engine.IMAGE_BATCH), default="first", advanced=True,
                               tooltip="Send the first image of a batch, or all of them in order."),
                io.Boolean.Input("stream", default=True, advanced=True, tooltip="Show the text on the node as it's written."),
                io.Boolean.Input("debug", default=False, advanced=True, tooltip="Print the full request and reply to the console."),
                # sockets
                io.String.Input("idea", optional=True, force_input=True,
                                tooltip="Your idea from another node. When connected (and its node isn't bypassed), "
                                        "this text is used instead of the words box."),
                io.Image.Input("image", optional=True,
                               tooltip="The picture. Connected and on: the preset's picture instruction is used. "
                                       "Not connected, bypassed or muted: its words instruction."),
                io.Clip.Input("clip", optional=True,
                              tooltip="Optional: your text encoder, to count the prompt in its own tokens and warn past token_limit."),
                io.String.Input("instruction_override", optional=True, force_input=True,
                                tooltip="Replaces the preset's instruction for this run."),
            ],
            outputs=[
                io.String.Output("prompt", display_name="prompt", is_output_list=True,
                                 tooltip="The rewritten prompt, trimmed. A list when one_per_line or variations is used."),
                io.String.Output("negative", display_name="negative", is_output_list=True,
                                 tooltip="A matching negative prompt, for presets that ask for one."),
                io.String.Output("thinking", display_name="thinking", is_output_list=True,
                                 tooltip="The model's reasoning when thinking is on."),
                io.String.Output("request", display_name="request", is_output_list=True,
                                 tooltip="The exact request sent, images shown as [image 1: WxH]."),
                io.String.Output("info", display_name="info", is_output_list=True,
                                 tooltip="Model, endpoint, tokens in and out, speed, time."),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def validate_inputs(cls, preset, model):
        # The model list lives in LM Studio and the preset list on disk, so both are checked here
        # instead of against the lists the node had when ComfyUI started.
        try:
            presets.LIBRARY.get(preset)
        except presets.PresetError as e:
            return str(e)
        return True

    @classmethod
    def fingerprint_inputs(cls, preset, **kwargs):
        # Editing a preset file reruns the node even though its name stays the same.
        try:
            return presets.LIBRARY.get(preset).digest
        except presets.PresetError:
            return preset

    @classmethod
    def execute(cls, preset, hold=False, image=None, clip=None, instruction_override=None, idea=None,
                **kwargs) -> io.NodeOutput:
        uid = str(cls.hidden.unique_id) if cls.hidden is not None else ""
        if hold:
            saved = HELD.get(uid) or _saved_in_workflow(cls.hidden.extra_pnginfo if cls.hidden else None, uid)
            if not saved or not saved.get("prompt"):
                raise ValueError("Hold is on, but this node has no prompt yet. Turn hold off and run once.")
            return _output(saved, held=True)

        p = presets.LIBRARY.get(preset)
        opts = engine.options(**kwargs, instruction_override=instruction_override, idea=idea)

        import comfy.model_management as mm

        try:
            from server import PromptServer

            server = PromptServer.instance
        except Exception:
            server = None

        def on_stream(text, thinking):  # live text on the node face
            if server is not None:
                server.send_sync(STREAM_EVENT, {"node": uid, "text": text, "thinking": thinking})

        runner = engine.Runner(opts, p, image=image, clip=clip, should_cancel=mm.processing_interrupted,
                               on_stream=on_stream, token=engine.api_token())
        try:
            result = runner.run()
        except lm.Cancelled:
            mm.throw_exception_if_processing_interrupted()
            raise
        except (engine.UserError, lm.LMStudioError) as e:
            raise RuntimeError(str(e)) from None

        saved = {name: result.column(name) for name in OUTPUT_NAMES}
        saved["warnings"] = result.warnings
        HELD[uid] = saved
        return _output(saved, held=False)


def _output(saved: dict, held: bool) -> io.NodeOutput:
    cols = {name: list(saved.get(name) or [""]) for name in OUTPUT_NAMES}
    n = len(cols["prompt"])
    for name in OUTPUT_NAMES:  # every list as long as the prompt list
        cols[name] = (cols[name] + [""] * n)[:n]
    if held:
        cols["info"] = [("held · " + i) if i else "held" for i in cols["info"]]
    ui = {"lmse": [{"prompt": cols["prompt"], "negative": cols["negative"], "info": cols["info"],
                    "thinking": cols["thinking"], "warnings": list(saved.get("warnings") or []), "held": held}]}
    return io.NodeOutput(*(cols[name] for name in OUTPUT_NAMES), ui=ui)


def _saved_in_workflow(extra_pnginfo, uid: str) -> dict | None:
    """The last result the panel saved into the workflow (node properties), for hold after a restart."""
    try:
        workflow = (extra_pnginfo or {}).get("workflow") or {}
        graphs = [workflow] + list((workflow.get("definitions") or {}).get("subgraphs") or [])
        last = uid.split(":")[-1]
        for graph in graphs:
            for node in graph.get("nodes") or []:
                if str(node.get("id")) == last:
                    saved = (node.get("properties") or {}).get(SAVED_PROPERTY)
                    if isinstance(saved, dict) and saved.get("prompt"):
                        return {k: v if isinstance(v, list) else [v] for k, v in saved.items()}
    except (AttributeError, TypeError):
        return None
    return None
