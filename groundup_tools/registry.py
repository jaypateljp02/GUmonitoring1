import inspect
import json
import logging
from typing import Dict, Any, Callable, List, Optional
from sqlalchemy.orm import Session

logger = logging.getLogger("groundup_tools.registry")


class ToolRegistry:
    """
    Universal MCP Tool Registry for Ground Up & Sugam Platform.
    Stores tool definitions, generates standard MCP manifests, and executes tools safely.
    """

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        func: Callable
    ):
        """Registers a tool with its MCP schema definition."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": parameters_schema,
            "func": func
        }
        logger.info(f"🛠️ [MCP Registry] Registered tool '{name}'")

    def get_manifest(self) -> List[Dict[str, Any]]:
        """Returns the standard MCP tool manifest for LLM tool calling."""
        manifest = []
        for name, t in self._tools.items():
            manifest.append({
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"]
            })
        return manifest

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        db: Session,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Invokes a registered tool by name with arguments and DB session."""
        if tool_name not in self._tools:
            return {"error": f"Tool '{tool_name}' not found in MCP registry."}

        tool_meta = self._tools[tool_name]
        func = tool_meta["func"]

        try:
            # Check if function expects db or context in kwargs
            sig = inspect.signature(func)
            kwargs = {**arguments}
            if "db" in sig.parameters:
                kwargs["db"] = db
            if "context" in sig.parameters:
                kwargs["context"] = context or {}

            if inspect.iscoroutinefunction(func):
                result = await func(**kwargs)
            else:
                result = func(**kwargs)

            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"Error executing MCP tool '{tool_name}': {e}", exc_info=True)
            return {"error": str(e), "success": False}


mcp_registry = ToolRegistry()


def register_tool(name: str, description: str, parameters_schema: Dict[str, Any]):
    """Decorator to register a function as an MCP tool."""
    def decorator(func: Callable):
        mcp_registry.register(name, description, parameters_schema, func)
        return func
    return decorator
