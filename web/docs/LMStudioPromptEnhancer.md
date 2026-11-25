# LM Studio Prompt Enhancer

*Prompts made to order, no sugar-coating: you always see exactly what the model was sent.*

Sends your words and/or a picture to a model running in LM Studio, with a saved instruction (a preset), and returns the rewritten prompt. What you see in the preset and your input is exactly what the model receives; the **request** output shows the whole thing.

## Getting started

- Start LM Studio's local server (Developer tab) and load your model, e.g. Gemma 4 31B. The node never loads or unloads models.
- Click the status line at the top of the node to test the connection, then pick the model.
- Pick a preset, type your idea (or connect text to the **idea** socket), and wire **prompt** into your text encoder.

## Words or picture

- The node works **words → text** by default.
- Connect an image and it switches to **picture → text**: the preset's picture instruction is used and the words box becomes your changes (optional).
- Bypass (Ctrl+B), mute or disconnect the image node and it switches back. The chip under the preset shows which mode the next run will use, and the info line says which one was used.
- The **idea** socket takes your idea from another node. While it's connected and its node is on, the words box is ignored (it's greyed out).

## Presets

- A preset is a Markdown file: a short header, then the instruction as plain text.
- Shipped presets are in the node's `presets` folder. Your own go in `ComfyUI/user/default/lmstudio-prompt-enhancer/presets` (the folder button opens it). A file there with the same name replaces the shipped one, and updates never touch it.
- Edits apply on the next run, no restart needed. The reload button refreshes the dropdown after you add a file.
- A preset can hold two instructions: everything under `=== words ===` (or before any marker) is used for words → text, everything under `=== picture ===` for picture → text. `picture_user_template` is the user message with a picture.
- A preset without sections uses one instruction for both, as `mode:` says: `both` (default), `words` (ignores pictures) or `picture` (needs one).
- `settings:` are starting values loaded when you pick the preset; whatever you change on the node afterwards is what's sent.

```
---
name: My preset
description: One line shown under the dropdown.
user_template: "The user's idea: {words}"
picture_user_template: "Describe this picture.\nThe user's changes: {words}"
settings:
  temperature: 0.7
slots:
  style: "film noir"
---
=== words ===
You are a prompt writer... Write {length}. Use this style: {style}.

=== picture ===
Look at the picture and write a prompt of {length}...
```

## Slots

- `{words}`: your text (from the words box or the idea socket). `{length}`: the Length choice. `{trigger}`: your trigger words.
- Any other slot listed under `slots:` gets its own field on the node.
- A line whose slots are all empty is dropped, so optional parts disappear cleanly.

## Prompt quality

- **Length** matches MOSS-Image's training captions: short (about 21 tokens), medium (about 77), long (about 235).
- Text in "double quotes" and your trigger words must come back letter for letter.
- **Checks** (Advanced) look at word count, "no/not" phrasing, labels like "Lighting:", and protected text. If one fails, the node retries once with a new seed and warns if it still fails.
- Choices in braces like `{red|green|blue}` are picked by the seed before sending.
- Connect your text encoder to **clip** to count the prompt in its own tokens and warn past the token limit.

## Parameters

- Each parameter has a tick. Off sends nothing, so LM Studio's own setting for the model applies. On sends the node's value.
- **Thinking** off is fastest. When it's on, **thinking room** is added to max tokens so reasoning can't use up the prompt's room.
- **Seed** fixed gives the same prompt every queue; randomize gives a new one.
- **Hold** freezes the last prompt without calling LM Studio. The last result is also saved in the workflow and in every PNG it makes.

## Outputs

- **prompt**: the rewritten prompt. **negative**: for presets that ask for one. **thinking**: the model's reasoning.
- **request**: the exact request sent, with images shown as `[image 1: 1024x1024]`. **info**: model, endpoint, tokens, speed, time.
- With **one per line** or **variations**, every output is a list, one item per prompt.

## When something goes wrong

- "LM Studio isn't answering": start the server in LM Studio, or check the server URL under the gear.
- "isn't loaded": load the model in LM Studio; the node won't do it for you.
- **On error: Pass through** sends your original words on and warns, so a long batch keeps going.
- Each run logs one line in the console; turn on Debug for the full request and reply.
