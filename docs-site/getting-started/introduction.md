# Introduction

AgentCore Starter is a production-ready template for building AWS-native AI agent backend services.

## What's included

- **FastAPI** management REST API with Google OAuth login
- **AWS Lambda** + Function URL hosting
- **DynamoDB** single-table storage
- **CloudFront** + S3 CDN for the management UI
- **React** management SPA (Vite + shadcn/ui)
- **AWS CDK** (Python) infrastructure as code

## Next steps

- [Quick start](/getting-started/quick-start) — deploy to your AWS account
- [Building agents](/agents/overview) — call the Bedrock endpoints and consume SSE streams
- [Sessions](/agents/sessions) — multi-turn conversations, session scoping, tool-calling
- [Security and secrets](/operations/security) — operational secret contracts and rotation procedures
- [Operational endpoints](/operations/endpoints) — `/health` liveness probe and `/api/csp-report` violation receiver
