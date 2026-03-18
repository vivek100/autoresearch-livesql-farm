# ReAct + Tracing Research Notes

This note records references used to simplify runtime and separate tracing concerns.

## Findings

1. `create_react_agent` is a prebuilt loop that already handles tool-calling control flow.
2. OpenInference provides LangChain instrumentation that captures LangGraph/LangChain spans without custom callback plumbing.
3. Phoenix can ingest OTLP traces locally and serve them for debugging/RCA.
4. Local callback traces can remain as sidecar artifacts, but prebuilt OTLP tracing should be the default backend.

## Sources

1. LangGraph prebuilt reference:
   https://reference.langchain.com/python/langgraph/agents/#langgraph.prebuilt.chat_agent_executor.create_react_agent
2. OpenInference LangChain instrumentation:
   https://arize-ai.github.io/openinference/python/instrumentation/openinference-instrumentation-langchain/
3. Phoenix docs:
   https://arize.com/docs/phoenix
4. LangGraph observability (LangSmith reference path):
   https://docs.langchain.com/oss/python/langgraph/observability
5. Local installed source inspected in venv:
   `.venv/Lib/site-packages/langgraph/prebuilt/chat_agent_executor.py`
