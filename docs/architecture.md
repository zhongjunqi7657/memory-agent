# Architecture Notes

The first version keeps four boundaries explicit:

```text
React client
    |
FastAPI API / SSE
    |
LangGraph agent runtime ---- MemoryService ---- PostgreSQL + pgvector
    |                                |
Qwen adapters                 extraction_jobs Worker
```

The business tables remain owned by the application. LangGraph checkpoints are a separate persistence concern and are introduced only after the basic graph is testable.

