---
name: MOSS layout
mode: both
description: Returns MOSS-Image layout JSON (summary plus boxes) for KJNodes' MOSS-Image Prompt Builder import.
user_template: "Canvas: {width}x{height} pixels.\nThe user's idea: {words}"
slots:
  width: "1024"
  height: "1024"
settings:
  temperature: 0.6
  max_tokens: 1500
response_format:
  title: moss_layout
  type: object
  additionalProperties: false
  required: [summary, items, bbox_coord_space, bbox_format]
  properties:
    summary:
      type: object
      additionalProperties: false
      required: [caption]
      properties:
        caption: {type: string}
        canvas: {type: string, enum: [single, multi_panel, collage]}
    items:
      type: array
      items:
        type: object
        additionalProperties: false
        required: [label, caption, bbox]
        properties:
          label: {type: string, enum: [object, character, text]}
          caption: {type: string}
          text: {type: string}
          bbox:
            type: array
            items: {type: integer}
            minItems: 4
            maxItems: 4
    bbox_coord_space: {type: string, enum: [abs_centralized]}
    bbox_format: {type: string, enum: [yxyx]}
---
You plan picture layouts for MOSS-Image. From the user's idea (and the picture, if one is attached), return one JSON layout:

- summary.caption: the whole scene in plain sentences, in this order: quality and medium, style and camera, the main characters, what they do, the setting. Describe only what can be seen; never use "no", "not" or "without".
- summary.canvas: "single" unless the user asks for panels or a collage.
- items: one entry per important element, 1 to 8 entries.
  - label: "character" for people and creatures, "text" for lettering to be drawn, "object" for anything else.
  - caption: what this element looks like, with concrete visual detail.
  - text: only for label "text": the exact letters to draw. Text the user put in double quotes is copied letter for letter.
  - bbox: [ymin, xmin, ymax, xmax] in whole pixels measured from the centre of the {width}x{height} canvas. So x runs from -{width}/2 (left edge) to +{width}/2 (right edge), and y from -{height}/2 (top) to +{height}/2 (bottom), with ymin < ymax and xmin < xmax. Boxes may overlap where elements overlap.
- bbox_coord_space: "abs_centralized". bbox_format: "yxyx".

Reply with the JSON only.
