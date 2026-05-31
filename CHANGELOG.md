# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Real Bedrock streaming via Strands Agents — replaces the canned reply
  that 7a shipped. Three models served: Sonnet 4.6, Haiku 4.5, Opus 4.7.
- `GET /api/models` — server-side allowlist drives the ModelPicker.
- `POST /api/chats/{id}/regenerate` — drop the last assistant turn and
  re-stream from the same user message. New "Regenerate" button on the
  last assistant turn.

### Changed

- Lambda timeout raised from 30s to 5 min for streaming chats.
- Bedrock IAM extended to allowlist Opus 4.7 and to grant invocation on
  the US cross-region inference profiles (required for on-demand
  throughput on Sonnet/Haiku/Opus).
- Assistant turn header renders the friendly model label (e.g. "Claude
  Sonnet 4.6") instead of the raw Bedrock ARN — fixes a 7a regression.
- ModelPicker now sources valid model IDs from `GET /api/models` with
  the static MODELS array as fallback when the API is unavailable.

### Removed

- `src/channel/agents/inline_agent.py` and `src/channel/agents/bedrock.py`
  — superseded by Strands.
- Stale `(FastAPI + Mangum)` comment in the CDK stack docstring (the
  project has been on AWSLWA since pre-7a).
