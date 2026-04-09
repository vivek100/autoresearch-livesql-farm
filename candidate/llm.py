"""LLM creation for OpenAI GPT models."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def create_llm():
    """Create the LangChain chat model using OpenAI."""
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        temperature=0.0,
        max_tokens=4096,
        request_timeout=60,
    )
