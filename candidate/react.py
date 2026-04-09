"""LangGraph ReAct agent builder with safe tool wrapping."""

from __future__ import annotations


def wrap_tools_safe(tools: list) -> list:
    """Wrap tools so exceptions come back as text instead of crashing the run."""
    from langchain_core.tools import StructuredTool

    wrapped_tools = []
    for tool in tools:
        original_func = getattr(tool, "func", None)
        if original_func is None:
            wrapped_tools.append(tool)
            continue

        def _make_safe(fn, tool_name):
            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    return f"Error in {tool_name}: {type(exc).__name__}: {str(exc)[:1000]}"

            return wrapper

        safe_func = _make_safe(original_func, tool.name)
        wrapped_tools.append(
            StructuredTool.from_function(
                func=safe_func,
                name=tool.name,
                description=tool.description,
                args_schema=getattr(tool, "args_schema", None),
            )
        )
    return wrapped_tools


def build_react_agent(llm, tools: list, system_prompt: str):
    """Build a LangGraph ReAct agent. Simple — no Kimi compat needed for GPT."""
    from langgraph.prebuilt import create_react_agent

    safe_tools = wrap_tools_safe(tools)
    return create_react_agent(model=llm, tools=safe_tools, prompt=system_prompt)
