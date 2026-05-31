# Building agents

::: warning
This page is being rewritten. As of Phase 7b, Channel's agent layer is
[Strands Agents](https://strandsagents.com/) over Bedrock — the
`starter.agents.bedrock` and `starter.agents.inline_agent` modules
described in earlier revisions of this page no longer exist.

For the current architecture, see:

- Spec: [Bedrock + AgentCore Memory chat backend design](https://github.com/warlordofmars/channel/blob/main/docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md)
- Source: `src/channel/agents/chat_agent.py` (Strands `Agent` factory) and
  `src/channel/agents/strands_sse.py` (event translator)
- The end-user surface is the chat app at `/app`, backed by
  `POST /api/chats/{id}/messages` (SSE streaming).
:::

## What changed

- **Removed:** `starter.agents.bedrock` (raw Converse) and
  `starter.agents.inline_agent` (inline-agent wrapper).
- **Added:** A single `chat_agent.build_agent()` helper that returns a
  Strands `Agent` with `BedrockModel`. Streaming is the only shape
  (Strands' `agent.stream_async`).
- **Three Anthropic models** are exposed via `GET /api/models`:
  Sonnet 4.6, Haiku 4.5, Opus 4.7 (all served via US cross-region
  inference profiles for on-demand throughput).
- The default model is set on a per-chat basis when the chat is
  created (and overridable per turn) — no longer a Lambda env var.

## AgentCore Memory

Not wired yet. Phase 7c adds long-term memory via boto3 calls into a
custom Strands `HookProvider` — see the
[spike report](https://github.com/warlordofmars/channel/blob/main/docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md)
for the design.
