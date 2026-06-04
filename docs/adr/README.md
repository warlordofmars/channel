# Architecture Decision Records

Short, durable records of significant technical decisions made in this project.

## Format

Each ADR is a markdown file named `NNNN-short-title.md` with this structure:

```markdown
# ADR-NNNN: Title
Date: YYYY-MM-DD  
Status: Accepted | Deprecated | Superseded by ADR-XXXX

## Context
What situation or problem prompted this decision?

## Decision
What did we decide to do?

## Consequences
What are the trade-offs, follow-on work, or constraints this decision creates?
```

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-bedrock-converse-api.md) | Bedrock Converse API as the agent LLM interface | Accepted |
| [0002](0002-streaming-lambda-web-adapter.md) | Streaming via AWS Lambda Web Adapter | Accepted |
| [0003](0003-inline-agent-and-session-memory.md) | Inline Agent and session memory conventions | Accepted |
| [0004](0004-agentcore-runtime-feasibility.md) | AWS Bedrock AgentCore Runtime + Memory feasibility for chat-app fork | Accepted |
| [0005](0005-orchestrator-agent.md) | Orchestrator agent for cross-session sequencing and delegation | Accepted |
| [0006](0006-skills-system.md) | Skills system for agent-loaded reference material | Accepted |
| [0007](0007-orchestrator-protocol-codification.md) | Codify orchestrator protocols surfaced during skills epic | Accepted |
| [0008](0008-issue-worker-push-discipline.md) | Issue-worker push discipline against wholesale-push damage | Accepted |
| [0009](0009-unified-context-budget.md) | Unified context budget across history, recall, attachments, and tool results | Accepted |
| [0010](0010-electron-release-pipeline.md) | Electron desktop release pipeline + macOS signing | Accepted |
