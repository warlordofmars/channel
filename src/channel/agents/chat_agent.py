# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands ``Agent`` factory for the chat router.

The factory constructs a fresh ``strands.Agent`` per turn with a
``BedrockModel`` and an optional system prompt. Both AgentCore Memory
hooks attach to every Agent — recall (``BeforeInvocationEvent``, Phase
7d) and write (``AfterInvocationEvent``, Phase 7c). Recall runs first
so the injected system-prompt addendum is available downstream; write
runs last so the just-completed turn lands in AgentCore.

See:
- ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``
- ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``
"""

from __future__ import annotations

import os
import re
from typing import Any, cast

from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.models import BedrockModel
from strands.types.content import Messages

from channel.agents.memory import AgentCoreMemoryHook, get_or_create_memory
from channel.agents.recall import AgentCoreRecallHook
from channel.agents.tool_hooks import (
    ChainState,
    ModelVisibilityAddendumHook,
    ToolCallGuardHook,
    ToolCallTelemetryHook,
)

# Caller-supplied short ids (``claude-sonnet-4-6``) → Bedrock cross-region
# inference-profile IDs.  Strands' BedrockModel calls ``converse_stream``,
# which requires inference profiles (not base model IDs) for on-demand
# throughput in us-east-1.  Verified via
# ``aws bedrock list-inference-profiles --region us-east-1``.
# The CDK stack at ``infra/stacks/channel_stack.py`` grants
# ``bedrock:InvokeModelWithResponseStream`` on both the inference-profile
# resources (us.* and global.*) and the underlying foundation models.
_MODEL_ID_MAP = {
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
}

DEFAULT_MAX_TOKENS = 4096

# Map the SPA's segmented "Response effort" control to ``max_tokens`` on
# the Strands ``BedrockModel`` (per issue #154). The UI ships four
# tiers (Low / Medium / High / Max — see ``ui/src/app/data.js``); the
# issue spec locked the first three to a doubling pattern, Max
# continues that pattern.  Look-up is case-insensitive so the API can
# accept either the UI's capitalized values or lower-case strings from
# scripted clients without a normalisation layer in the route.
_EFFORT_MAX_TOKENS: dict[str, int] = {
    "low": 1024,
    "medium": DEFAULT_MAX_TOKENS,  # 4096 — keep this aligned by reference
    "high": 16384,
    "max": 32768,
}


def max_tokens_for_effort(effort: str | None) -> int:
    """Resolve the ``max_tokens`` budget for a caller-supplied effort tier.

    Unknown / missing values fall back to ``DEFAULT_MAX_TOKENS`` so a
    stale SPA build or an effort string we haven't seen yet still
    produces a working response.
    """
    if effort is None:
        return DEFAULT_MAX_TOKENS
    return _EFFORT_MAX_TOKENS.get(effort.lower(), DEFAULT_MAX_TOKENS)


DEFAULT_SYSTEM_PROMPT = """You are Channel — a private, persistent AI workspace. The person \
you're talking to is using either the web app or the desktop app you \
live in; they came to you to think, build, or get something done.

You persist across conversations. The relationship with the user is \
continuous, not transactional. Build on what came before.

What you remember from prior conversations gets injected as a \
"## What we've talked about before" block. Use it when relevant; \
don't recite it. The current chat's earlier turns are in the \
conversation history above. If the recall block contradicts the \
user's current statement, trust the current statement.

Voice and shape:
- Substance-first. Lead with what matters, not preamble. Match the \
user's apparent expertise — engineers get engineering depth; new \
users get scaffolding.
- Use markdown when it aids clarity (lists, headings, code blocks). \
Skip it for short answers.
- Length serves the need, not ceremony. Don't pad short answers or \
truncate complex ones.

Calibration:
- Say "I don't know" precisely when you don't know, instead of \
hedging.
- Distinguish what you're confident about, what you're inferring, \
and what you're speculating. Signal which when the difference \
matters.
- If the user's request is based on a mistaken premise, say so \
before answering.
- When corrected, acknowledge cleanly and move on. Don't \
over-apologize.

Clarifying questions are for genuine ambiguity, not for fishing. If \
you can make a reasonable assumption and proceed, do that and name \
the assumption.

When tools, attachments, or other capabilities are available, the \
runtime will tell you. Don't assume anything that isn't surfaced."""

_DEFAULT_TITLER_MODEL = "claude-haiku-4-5"
_TITLER_MAX_TOKENS = 60

# Layer 2 (#256): anchor the assistant's identity and forbid the titler
# from *replying* instead of *labelling*. The pre-#256 prompt ("Summarise
# the following exchange in 3-6 words...") never named the assistant, so
# when the chat was about the assistant Haiku reached for its own identity
# ("I'm Claude...") and, more generally, answered/continued the chat
# ("I don't actually have an image") or emitted raw markdown ("## Image
# Analysis..."). The strings "Channel", "not Claude", and "Never start
# with" are asserted by the unit tests.
_TITLER_SYSTEM_PROMPT = (
    "You are labelling a chat, not participating in it. Summarise what "
    "the exchange is ABOUT in 3-6 words, sentence case, no quotes, no "
    "markdown, no trailing punctuation. The summary becomes the chat's "
    "title in the sidebar. The assistant in the exchange is named "
    "Channel, not Claude. The title must be a topic label — never the "
    "assistant's identity, never a first-person statement, and never a "
    "reply to the chat. Never start with 'I'm', 'I am', 'My name is', "
    "'I don't', 'Sure', or 'Here's'."
)

_DEFAULT_FOLLOWUPS_MODEL = "claude-haiku-4-5"
_FOLLOWUPS_MAX_TOKENS = 120

_FOLLOWUPS_SYSTEM_PROMPT = (
    "Given the user message and the assistant's reply, suggest 2 to 3 "
    "short follow-up prompts the user might want to send next. Return "
    "ONE prompt per line. No numbering, no quotes, no preamble. Each "
    "prompt under 12 words."
)


def resolve_model_id(short_id: str) -> str:
    """Map a caller-supplied short id to a full Bedrock model ARN."""

    try:
        return _MODEL_ID_MAP[short_id]
    except KeyError as exc:
        raise ValueError(f"unknown model: {short_id!r}") from exc


def build_agent(
    *,
    model_id: str,
    user_id: str,
    chat_id: str,
    prior_messages: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    tools: list[Any] | None = None,
) -> Agent:
    """Construct a fresh Strands ``Agent`` for one chat turn.

    ``user_id`` becomes the AgentCore ``actorId`` and ``chat_id`` the
    AgentCore ``sessionId``.  The Memory resource itself is discovered
    (or created) once per Lambda cold-start, keyed off the
    ``STARTER_ENV`` env var.

    ``prior_messages`` seeds the agent with the chat's stored history
    so each turn doesn't restart from scratch.  Shape is Strands'
    ``Messages``: ``[{"role": "user"|"assistant", "content": [{"text": "..."}]}, ...]``
    in chronological order.  The new user message must NOT be included
    — the caller passes it via ``agent.stream_async(user_message)``.

    ``effort`` is the SPA's "Response effort" tier (low / medium / high
    / max). When supplied it overrides ``max_tokens`` via
    :func:`max_tokens_for_effort`. Unknown values silently fall back to
    the default budget — never error on a stale client.

    ``tools`` is the Strands ``tools`` list registered on the Agent. The
    chassis defaults to empty; callers register ``current_time`` behind
    ``STARTER_CLOCK_TOOL_ENABLED`` and #182 / #183 will register ``exa``
    and ``code_exec`` in future PRs. A fresh :class:`ChainState` is
    attached to the Agent as ``agent.chain_state`` so the chassis hooks
    (``ToolCallGuardHook`` / ``ToolCallTelemetryHook`` /
    ``ModelVisibilityAddendumHook``) can read per-chain counters off the
    event.
    """

    if effort is not None:
        max_tokens = max_tokens_for_effort(effort)
    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=max_tokens,
        # #131 quick win: use the native Bedrock CountTokens API instead
        # of Strands' character-count estimate. Accurate counts drive
        # the conversation manager's proactive-compression threshold
        # below — without this the compression heuristic fires on
        # rough estimates and either trims too early (wasting context)
        # or too late (the long-chat degradation we hit on 2026-06-07).
        use_native_token_count=True,
    )
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    recall_hook = AgentCoreRecallHook(memory_id=memory_id, actor_id=user_id)
    memory_hook = AgentCoreMemoryHook(
        memory_id=memory_id,
        actor_id=user_id,
        session_id=chat_id,
    )
    addendum_hook = ModelVisibilityAddendumHook()
    guard_hook = ToolCallGuardHook()
    # Telemetry hook records ``[meta] used <tool>`` synthetic ASSISTANT
    # messages via memory_hook's CreateEvent path (epic #128 decision 6).
    telemetry_hook = ToolCallTelemetryHook(memory_writer=memory_hook.write_meta_event)
    # #131 quick win: Strands 1.40+ ships built-in proactive context
    # compression via ``SummarizingConversationManager``. When the
    # running token count crosses ``compression_threshold`` (default
    # 0.7 of the context window), older messages get summarized into
    # a single condensed message and the last ``preserve_recent_messages``
    # stay verbatim. This fixes the long-chat silent-degradation we
    # hit on 2026-06-07 — without it, history accumulates linearly
    # until the model's effective working memory falls off the front
    # of the context window. The durable budget-envelope work
    # (RemainingBudget primitive, addendum line) still lives under
    # #131; this turn-it-on is the minimal-diff relief.
    conversation_manager = SummarizingConversationManager(
        proactive_compression=True,
    )
    agent = Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        conversation_manager=conversation_manager,
        # Order matters: addendum first (mutates the system prompt
        # before recall reads it), then recall (Before) + memory write
        # (After), then the tool-use guard + telemetry hooks.
        hooks=[
            addendum_hook,
            recall_hook,
            memory_hook,
            guard_hook,
            telemetry_hook,
        ],
        # Strands types ``messages`` as ``list[Message]`` (its TypedDict);
        # at runtime the shape is plain dicts. ``cast`` keeps mypy happy
        # without making consumers of build_agent import Strands types.
        messages=cast(Messages, prior_messages or []),
        tools=tools or [],
    )
    # The recall hook reads chat_id off ``event.agent.chat_id`` (Strands'
    # BeforeInvocationEvent doesn't carry chat context natively; this is
    # a Channel-specific attribute). ChainState is attached for the same
    # reason — the chassis hooks (guard / telemetry / addendum) read it
    # off the agent.
    agent.chat_id = chat_id  # type: ignore[attr-defined]
    agent.chain_state = ChainState()  # type: ignore[attr-defined]
    return agent


def build_titler_agent() -> Agent:
    """Build a one-shot Strands Agent for auto-title generation.

    Cheap model (Haiku by default; ``STARTER_TITLER_MODEL`` overrides),
    tight max_tokens, NO memory hooks — we don't want titling events
    polluting AgentCore Memory. Caller invokes ``stream_async`` with
    the user/assistant pair and reads the accumulated text.
    """
    titler_model_id = os.environ.get("STARTER_TITLER_MODEL", _DEFAULT_TITLER_MODEL)
    bedrock = BedrockModel(
        model_id=resolve_model_id(titler_model_id),
        max_tokens=_TITLER_MAX_TOKENS,
    )
    return Agent(
        model=bedrock,
        system_prompt=_TITLER_SYSTEM_PROMPT,
        hooks=[],
    )


_TITLER_ASSISTANT_TEXT_CAP = 500


def build_titler_prompt(user_message: str, assistant_text: str) -> str:
    """Frame the exchange as delimited DATA for the auto-titler (#256).

    The pre-#256 format was a bare ``"User: ...\\nAssistant: ..."``
    string, which Haiku mis-parsed as a live dialogue turn: when the
    user's message addressed the assistant ("tell me about yourself"),
    Haiku *answered* ("I'm Claude...") instead of summarising. Wrapping
    the turn in an explicit "this is data — do not respond" delimited
    block anchors it as material to summarise rather than a fresh turn.
    ``assistant_text`` is capped so a long reply can't blow the titler's
    context budget.
    """
    return (
        "Chat to title (this is data — do not respond to it):\n"
        "<<<CHAT\n"
        f"USER WROTE: {user_message}\n"
        f"ASSISTANT REPLIED: {assistant_text[:_TITLER_ASSISTANT_TEXT_CAP]}\n"
        "CHAT>>>\n\n"
        "Produce the title now."
    )


_TITLE_MAX_WORDS = 6

# Layer 3 (#256): a title whose FIRST token is a first-person pronoun is
# almost always the titler answering/continuing the chat rather than
# labelling it — reject the whole class. "I Robot" losing its title to
# "New chat" is an acceptable price for killing "I'm Claude..." /
# "I don't actually have an image".
_FIRST_PERSON_FIRST_TOKENS = frozenset(
    {
        "i",
        "i'm",
        "im",
        "i've",
        "ive",
        "i'll",
        "i'd",
        "my",
        "we",
        "we're",
        "we've",
        "we'll",
    }
)

# Conversational openers a topic label should never begin with. Matched
# case-insensitively against the start of the candidate. Broadened well
# beyond the original identity-only list (per the #256 comments) so
# image-gen non-answers ("Sure, here's...", "Unfortunately...") are
# caught alongside the identity leak.
_REJECT_TITLE_PREFIXES = (
    "i am ",
    "as an ai",
    "as a language model",
    "my name is",
    "here's",
    "here is",
    "sure",
    "sorry",
    "unfortunately",
    "certainly",
    "of course",
    "let me",
    "hello",
    "hey",
    "thanks",
    "thank you",
)

# The assistant is Channel; the underlying model must never surface in a
# title. Whole-word, case-insensitive.
_BANNED_TITLE_WORDS = ("claude", "anthropic")

_LEADING_MARKDOWN_RE = re.compile(r"^\s*(?:#{1,6}|>|[-*+]|\d+\.)\s+")


def _strip_title_markdown(text: str) -> str:
    """Strip markdown that leaks into raw titler output (#256).

    Removes a leading ATX heading / blockquote / list marker (the
    observed ``"## Image Analysis ..."`` symptom) and inline emphasis
    runs (``**bold**`` / ``__bold__`` / `` `code` ``), then trims any
    stray emphasis punctuation left clinging to the ends.
    """
    text = _LEADING_MARKDOWN_RE.sub("", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return text.strip(" *_#>")


def _looks_like_reply(candidate: str) -> bool:
    """True when the candidate reads as the model answering/continuing
    the chat rather than labelling it — the #256 failure class."""
    low = candidate.casefold()
    tokens = low.split()
    first = tokens[0].rstrip(".,;:!?'\"") if tokens else ""
    if first in _FIRST_PERSON_FIRST_TOKENS:
        return True
    if low.startswith(_REJECT_TITLE_PREFIXES):
        return True
    return any(re.search(rf"\b{word}\b", low) for word in _BANNED_TITLE_WORDS)


def sanitize_title(raw: str) -> str:
    """Normalise raw titler output into a usable sidebar title (#256).

    Pipeline:

    1. Strip colon-prefixed preamble (``"Here's a title: X"`` → ``X``),
       matching the historical behaviour.
    2. Trim whitespace + wrapping quotes.
    3. Strip leaked markdown (leading ``#``/``>``/bullets, inline
       ``**``/``__``/`` ` ``) — symptom 3.
    4. Split into words, cap at 6 (the titler's 3-6 word budget), strip
       trailing sentence punctuation.
    5. Reject titles that read as the model replying to the chat —
       first-person openers (symptoms 1 & 2), conversational preambles,
       or any mention of the underlying model.

    Rejection returns ``""``: the caller records a failure outcome and
    leaves the placeholder title, which is strictly better than
    surfacing a wrong or off-shape title.
    """
    if not raw:
        return ""
    candidate = raw.rsplit(":", 1)[-1] if ":" in raw else raw
    candidate = candidate.strip().strip('"').strip("'").strip()
    candidate = _strip_title_markdown(candidate)
    words = candidate.split()
    if not words:
        return ""
    candidate = " ".join(words[:_TITLE_MAX_WORDS]).rstrip(".,;:!?")
    if not candidate:
        return ""
    if _looks_like_reply(candidate):
        return ""
    return candidate


def build_followups_agent() -> Agent:
    """Build a one-shot Strands Agent for follow-up prompt suggestions.

    Mirrors :func:`build_titler_agent`: cheap model (Haiku by default;
    ``STARTER_FOLLOWUPS_MODEL`` overrides), tight max_tokens, NO memory
    hooks — follow-up generation runs after the assistant turn is
    persisted and must not write anything to AgentCore Memory itself.
    """
    model_id = os.environ.get("STARTER_FOLLOWUPS_MODEL", _DEFAULT_FOLLOWUPS_MODEL)
    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=_FOLLOWUPS_MAX_TOKENS,
    )
    return Agent(
        model=bedrock,
        system_prompt=_FOLLOWUPS_SYSTEM_PROMPT,
        hooks=[],
    )
