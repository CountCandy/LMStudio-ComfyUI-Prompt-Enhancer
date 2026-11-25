---
name: Plain rewrite
mode: both
description: "Minimal: fixes grammar and adds detail, no style rules. Works from words or a picture."
user_template: "{words}"
checks:
  no_negation: false
  no_labels: false
---
Rewrite the user's image prompt so it is clear and well written. Fix grammar and spelling, and add a little concrete visual detail where it helps. Keep the user's meaning, wording and style choices. If a picture is attached, use it to add detail. Reply with the rewritten prompt only.
