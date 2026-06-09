# Copyright (c) 2026 John Carter. All rights reserved.
"""Lambda sandbox package for code execution (#183).

Deployed as a separate Lambda function (``CodeExecLambda`` in
``infra/stacks/channel_stack.py``). The API Lambda invokes it via the
``code_exec`` Strands tool wrapper.
"""
