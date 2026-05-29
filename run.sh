#!/bin/bash
# Entrypoint for AWS Lambda Web Adapter (AWSLWA).
# AWSLWA intercepts Lambda invocations, translates them to HTTP requests,
# and forwards them to this web server running on port 8080.
# With AWS_LWA_INVOKE_MODE=response_stream and Function URL RESPONSE_STREAM
# mode, SSE responses are streamed directly to the caller.
exec python -m uvicorn channel.api.main:app --host 0.0.0.0 --port 8080
