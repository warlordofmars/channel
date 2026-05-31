# Sessions

::: warning
This page is being rewritten. As of Phase 7b, Channel doesn't use
Bedrock's per-session memory anymore — `inline_agent.py`'s
`f"{jwt_sub}:{session_id}"` sessionId namespacing is gone.

The current architecture:

- **Chats** are the unit of multi-turn conversation. Each chat is a
  DynamoDB row family at `PK=USER#{u}, SK=CHAT#{ts}#{id}` with
  child message rows at `PK=CHAT#{id}, SK=MSG#{ts}#{msg_id}`.
- **Per-turn agent construction**: every `POST /api/chats/{id}/messages`
  builds a fresh `strands.Agent` with `BedrockModel`. The agent is
  stateless across turns — chat history is read from DynamoDB by the
  router and (in Phase 7c onwards) injected into the system prompt via
  AgentCore Memory recall.
- **Ownership**: enforced at the API layer via
  `_load_owned_chat(chat_id, jwt_sub)` in `src/channel/api/chats.py` —
  mismatches return 404, not 403, so chat existence isn't leaked.

For the design rationale see:

- [Bedrock + AgentCore Memory chat backend design spec](https://github.com/warlordofmars/channel/blob/main/docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md)
- [Strands AgentCore Memory adapter spike](https://github.com/warlordofmars/channel/blob/main/docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md)
:::
