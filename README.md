# LM Studio Prompt Enhancer for ComfyUI

One ComfyUI node that sends your words and/or a picture to a model running in [LM Studio](https://lmstudio.ai) (made for Gemma 4 31B), with a saved instruction you pick, and returns the rewritten prompt. Its output is plain text for your usual text encoder (MOSS-Image, or anything else), so nothing downstream changes.

Nothing is added behind your back: what you see in the preset and your input is exactly what the model receives, and the **request** output shows the whole request.

- **Presets** are Markdown files you edit in any text editor. They decide whether they work from words, a picture, or both, and changes apply on the next run, with no restart.
- **Every LM Studio parameter** has an on/off tick. Off sends nothing, so LM Studio's own per-model settings apply.
- **Never loads or unloads models.** LM Studio keeps your model loaded; if it isn't, the node says so.
- **No extra Python packages.** It uses only what ComfyUI already ships, so there's no pip step.

## Install

1. Clone into ComfyUI's `custom_nodes` folder and restart ComfyUI:
   ```
   cd ComfyUI/custom_nodes
   git clone https://github.com/CountCandy/LMStudio-ComfyUI-Prompt-Enhancer
   ```
2. In LM Studio, load your model and start the local server (Developer tab, default `http://127.0.0.1:1234`).
3. Add **LM Studio Prompt Enhancer** (category *LM Studio*). Click the status line at the top of the node to test the connection, pick the model, pick a preset and type your idea.

Example workflows for MOSS-Image are in [`example_workflows`](example_workflows): one from words, one from a picture.

## The node

| Part | What it does |
| --- | --- |
| Status line | Click to test: is LM Studio answering, is the model loaded, does it accept images? |
| Preset | The saved instruction, with its description. Reload and Open folder buttons beside it. A preset's own slots (like `{style}`) show as fields here. |
| Words | Your idea, or in picture mode your changes. `{red\|green}` picks one option by the seed; text in "double quotes" must come back exactly. |
| Model card | Model, length (short/medium/long), trigger words, temperature, max tokens, seed, thinking, hold. |
| Advanced | top_p, top_k, min_p, repeat penalty, stop strings, thinking room, checks, variations, one idea per line, token limit, history, on error, JSON schema, extra JSON. |
| Gear | Server URL, API mode, API token, timeout, picture size and batch handling, streaming, debug, and a switch back to plain widgets. |
| Result | The latest prompt (with a copy button), any warnings, and an info line: model, endpoint, tokens, speed, time. |

Every setting is also an ordinary ComfyUI widget underneath. If the panel ever fails to load, the node still works and saves correctly. Right-click the node and choose **LM Studio: show plain widgets** to switch it off yourself.

**Inputs:** `image` (for picture presets), `clip` (optional: your text encoder, to count the prompt in its own tokens and warn past `token_limit`), `instruction_override` (replaces the preset's instruction for one run).

**Outputs:** `prompt`, `negative` (for presets that ask for one), `thinking`, `request` (images shown as `[image 1: 1024x1024]`), `info`. With *one per line* or *variations*, each output is a list.

## Presets

Shipped presets live in `presets/`. Your own go in `ComfyUI/user/default/lmstudio-prompt-enhancer/presets/` (the folder button opens it). Updates never touch that folder, and a file there with the same name replaces the shipped one.

```markdown
---
name: MOSS from words
mode: words            # words | picture | both
description: Turns a short idea into a MOSS-Image prompt.
user_template: "The user's idea: {words}"
settings:              # starting values loaded when you pick the preset
  temperature: 0.7
  max_tokens: 400
slots:                 # optional extra fields shown on the node
  style: ""
checks:                # optional: tune or switch off the prompt checks
  words: [100, 180]
  no_negation: true
  no_labels: true
---
You are a prompt writer for an image model... Write {length}. Style: {style}.
```

- **Slots:** `{words}` is your text, `{length}` the Length choice, `{trigger}` your trigger words. Anything listed under `slots:` gets its own field. A line whose slots are all empty is dropped cleanly.
- **What the model receives:** the system message is the preset's instruction; the user message is `user_template` with the slots filled, followed by the image(s) in picture mode. Nothing else.
- **Structured output:** `response_format:` takes a JSON schema (see *MOSS layout*).
- **Negative prompt:** `negative: true` splits the reply at `Negative:` onto the negative output.

| Shipped preset | Mode | Notes |
| --- | --- | --- |
| MOSS from words | words | Sentences ordered quality and medium, style and camera, character, action, setting. Never defaults to "photograph". |
| MOSS from picture | picture | Describes the picture the same way, then applies your words as changes. |
| MOSS layout | both | MOSS-Image layout JSON (summary plus boxes) for KJNodes' MOSS-Image Prompt Builder import. |
| Plain rewrite | both | Fix grammar and add detail, no style rules. |

## Prompt quality

- **Length** matches what MOSS-Image was trained on: captions of about 21, 77 and 235 tokens.
- **Protected text:** anything in double quotes and your trigger words must come back letter for letter.
- **Checks** look at word count, "no/not" phrasing, labels like "Lighting:", and protected text. If one fails, the node retries once with a new seed and warns if it still fails.
- **Hold** freezes the last prompt without calling LM Studio. The last result is saved in the workflow (and so in every PNG), so hold still works after a restart.
- **History:** every prompt is saved with its inputs and settings to `ComfyUI/user/default/lmstudio-prompt-enhancer/history.jsonl`.
- **Caching:** the node only calls LM Studio when something that affects the result changes, including the preset file's contents. A fixed seed gives the same prompt every queue; randomize gives a new one.
- **Cancel:** ComfyUI's Cancel button stops the request at once.

## Connection

- **API mode:** *OpenAI-compatible* (`/v1/chat/completions`) is the default. It's documented and stable, and also works with vLLM and llama.cpp. *Native* (`/api/v1/chat`) adds `min_p` and LM Studio's speed stats. *Auto* tries native and falls back.
- **Thinking:** off, low, medium or high is sent as `reasoning_effort` (OpenAI-compatible) or `reasoning` (native). *Model* sends nothing. If a server rejects a field, the node sends the request once more without it and says so in the info line.
- **API token:** set it under the gear or in ComfyUI's settings (*LM Studio Prompt Enhancer → Connection*). It is stored in ComfyUI's settings, not in the workflow, so shared workflows don't leak it. The `LMSTUDIO_API_TOKEN` environment variable also works.

## Errors

| Problem | What happens |
| --- | --- |
| Server not reachable | Stops with "LM Studio isn't answering at <url>". |
| Model not loaded | Stops with the model name and a hint to load it. |
| Picture preset without an image, or empty words | Stops before calling the server. |
| Empty or cut-off reply | Stops; the message says whether the token limit was hit. |
| Timeout | Stops after `timeout` seconds. |

Set **On error** to *Pass through* to send your original words on with a warning instead, so a long batch keeps going.

## Development

```
pip install pytest
python -m pytest            # from the repo folder; no GPU or LM Studio needed
python tests/fake_lmstudio.py --port 1234   # a fake LM Studio, to try the node in ComfyUI
```

| File | Contents |
| --- | --- |
| `__init__.py` | Registers the node, its routes and the `web` folder. |
| `node.py` | The node: inputs, outputs, hold, caching. |
| `engine.py` | One run, without ComfyUI: requests, checks, retries, history. |
| `client.py` | LM Studio calls: OpenAI-compatible, native, model list, test. |
| `media.py` | Image encoding; video-frame and audio helpers (not wired to the node yet). |
| `presets.py`, `prompt_tools.py` | Preset files; slots, choices, checks. |
| `routes.py` | Server routes the panel calls: models, test, presets, open folder. |
| `web/` | The panel (`panel.js`, `panel.css`) and the help page. |

## Credits

The panel's look follows [AusBoss nodes](https://github.com/ausboss/ComfyUI-AusBoss) (MIT). The MOSS layout format follows [KJNodes](https://github.com/kijai/ComfyUI-KJNodes)' MOSS-Image Prompt Builder.
