"""LM Studio Prompt Enhancer for ComfyUI.

Sends your words and/or a picture to a model running in LM Studio with a saved instruction, and
returns the rewritten prompt. No extra Python packages: only what ComfyUI already ships.
"""

WEB_DIRECTORY = "./web"

try:
    from comfy_api.latest import ComfyExtension, io
except ImportError:  # imported outside ComfyUI (tests, tools): nothing to register
    ComfyExtension = None

if ComfyExtension is not None:
    from .node import LMStudioPromptEnhancer

    class LMStudioPromptEnhancerExtension(ComfyExtension):
        async def on_load(self) -> None:
            from . import routes

            routes.register()

        async def get_node_list(self) -> list[type[io.ComfyNode]]:
            return [LMStudioPromptEnhancer]

    async def comfy_entrypoint() -> LMStudioPromptEnhancerExtension:
        return LMStudioPromptEnhancerExtension()

    __all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
