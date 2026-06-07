"""Phase 6 agentic layer: a LangGraph StateGraph (supervisor → vector/web → synthesis).

The compiled graph is stateless and shared on ``app.state``; per-request data (the user's LLM
provider, the Pinecone/embedder/web clients, and conversation history) flows through
``GraphState``. See docs/09_Phase6_Agentic_Architecture.md for the authoritative design.
"""
