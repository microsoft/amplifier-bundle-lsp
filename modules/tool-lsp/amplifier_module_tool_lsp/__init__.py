"""Generic LSP tool module - all language knowledge comes from configuration."""

from .tool import LspTool

__all__ = ["LspTool", "mount"]


async def mount(coordinator, config: dict):
    """Mount the LSP tool with configuration-driven language support.

    Returns cleanup function to properly shutdown LSP server subprocesses.
    """
    tool = LspTool(config)
    coordinator.mount_points["tools"][tool.name] = tool
    # Return cleanup function so coordinator can shutdown LSP servers on session end
    return tool.cleanup
