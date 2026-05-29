# Building agents

Channel ships two layers of Bedrock integration out of the box:

| Layer | Module | When to use |
|---|---|---|
| **Raw Converse** | `starter.agents.bedrock` | Simple single-turn prompts; full control over messages and token counts |
| **Inline Agent** | `starter.agents.inline_agent` | Multi-turn conversations with session memory; add action groups for tool-calling |

Both layers expose non-streaming and SSE streaming variants, and both run on the same Lambda function behind the same Function URL.

## Prerequisites

The Lambda IAM role already has the required permissions:

- `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` — for the Converse API
- `bedrock:InvokeInlineAgent` — for the inline agent API

The model is controlled by the `BEDROCK_MODEL_ID` environment variable (default: `anthropic.claude-sonnet-4-6`). Override it in CDK `common_env` or via SSM to switch models without a code change.

## The echo endpoints (Converse API)

These are scaffold endpoints. Replace them with your own logic once you understand the pattern.

### `POST /api/agents/echo`

Single-turn, buffered response.

```bash
curl -X POST https://your-domain/api/agents/echo \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the capital of France?", "system": "Be concise."}'
```

```json
{
  "reply": "Paris.",
  "input_tokens": 18,
  "output_tokens": 3
}
```

### `POST /api/agents/echo/stream`

Single-turn, SSE streaming. Each event is a JSON object on a `data:` line.

```bash
curl -X POST https://your-domain/api/agents/echo/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Count to five.", "system": "Use numerals only."}' \
  --no-buffer
```

```
data: {"type": "delta", "text": "1"}

data: {"type": "delta", "text": ", 2"}

data: {"type": "delta", "text": ", 3, 4, 5"}

data: {"type": "done", "stop_reason": "end_turn", "input_tokens": 14, "output_tokens": 9}
```

## The agent endpoints (Inline Agent)

These use `bedrock-agent-runtime.invoke_inline_agent` — the same model, but with session continuity between turns. See [Sessions](./sessions) for the full conversation flow.

### `POST /api/agents/invoke`

Single turn with optional session context, buffered response.

```bash
curl -X POST https://your-domain/api/agents/invoke \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, my name is Alice.", "instruction": "You are a helpful assistant."}'
```

```json
{
  "reply": "Hello Alice! How can I help you today?",
  "session_id": "a3f2c1d0-..."
}
```

Pass `session_id` back on the next turn to continue the conversation.

### `POST /api/agents/invoke/stream`

Multi-turn, SSE streaming. The `done` event carries the `session_id` to use on the next request.

```bash
curl -X POST https://your-domain/api/agents/invoke/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What did I just tell you my name was?", "session_id": "a3f2c1d0-..."}' \
  --no-buffer
```

```
data: {"type": "delta", "text": "You told me your name is"}

data: {"type": "delta", "text": " Alice."}

data: {"type": "done", "session_id": "a3f2c1d0-..."}
```

## Consuming SSE in JavaScript

```js
const resp = await fetch('/api/agents/invoke/stream', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ message, session_id: sessionId }),
});

const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const events = buffer.split('\n\n');
  buffer = events.pop(); // keep incomplete tail

  for (const event of events) {
    if (!event.startsWith('data: ')) continue;
    const payload = JSON.parse(event.slice(6));
    if (payload.type === 'delta') {
      appendText(payload.text);
    } else if (payload.type === 'done') {
      sessionId = payload.session_id; // save for next turn
    }
  }
}
```

::: tip CloudFront and SSE
The `/api/*` CloudFront behaviour routes to the Lambda Function URL.
CloudFront may buffer SSE responses depending on your distribution settings.
For reliable real-time streaming, call the Function URL directly — its address
is in the `ApiFunctionUrl` CloudFormation output.
:::

## Customising the scaffold

The echo and invoke endpoints are in `src/starter/api/agents.py`.
Replace or extend the request/response models and the calls to `converse()` /
`invoke()` with your own agent logic — tool-calling, RAG retrieval, multi-step
orchestration, etc.
