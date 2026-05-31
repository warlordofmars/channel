# Copyright (c) 2026 John Carter. All rights reserved.
"""
Invoke task definitions for Channel.

Usage:
    uv run inv --list                       # list all tasks
    uv run inv lint                         # lint + typecheck everything
    uv run inv fmt                          # auto-format Python source
    uv run inv test                         # run unit + integration + frontend tests
    uv run inv test-unit                    # unit tests only (no external deps)
    uv run inv test-integration             # integration tests (requires DynamoDB Local)
    uv run inv dev                          # start DynamoDB Local + API + UI dev servers
    uv run inv e2e                          # run e2e tests against deployed stack
    uv run inv e2e-local                    # run e2e tests against local dev stack (inv dev must be running)
    uv run inv deploy                       # deploy to AWS via CDK
    uv run inv synth                        # synthesize CDK template (no Docker bundling)
    uv run inv outputs                      # print CloudFormation stack outputs
    uv run inv install-hooks               # install pre-push hook (run once after clone)
    uv run inv pre-push                    # full local CI gate (lint+typecheck+unit+frontend)
"""

import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from invoke import task

ROOT = Path(__file__).parent
UI = ROOT / "ui"
DESKTOP = ROOT / "desktop"
INFRA = ROOT / "infra"
REGION = "us-east-1"
DYNAMO_CONTAINER = "channel-dynamo-local"
DYNAMO_PORT = 8000
API_PORT = 8001
UI_PORT = 5173


# ── Helpers ───────────────────────────────────────────────────────────────────


def _stack_name(env="prod"):
    return "ChannelStack" if env == "prod" else f"ChannelStack-{env}"


def _infer_next_version(ctx):
    """Infer the next semver from commits since the last tag using conventional commit rules."""
    try:
        last_tag = ctx.run("git describe --tags --abbrev=0", hide=True).stdout.strip()
    except Exception:
        last_tag = "v0.0.0"

    version = last_tag.lstrip("v")
    major, minor, patch = (int(x) for x in version.split("."))

    try:
        log = ctx.run(f"git log {last_tag}..HEAD --pretty=format:%s", hide=True).stdout.strip()
    except Exception:
        log = ""

    bump = "patch"
    for msg in log.splitlines():
        if re.search(r"^[a-z]+(\(.+\))?!:|BREAKING CHANGE", msg):
            bump = "major"
            break
        elif re.search(r"^feat(\(.+\))?:", msg) and bump != "major":
            bump = "minor"

    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def _aws_account(ctx) -> str:
    """Get the current AWS account ID via STS."""
    return ctx.run(
        "aws sts get-caller-identity --query Account --output text",
        hide=True,
    ).stdout.strip()


def _origin_verify_secret(ctx, env: str) -> str:
    """Read /channel/<env>/origin-verify-secret from SSM.

    Passed to the CDK stack as the ``origin_verify_secret`` context value so
    the CloudFront origin custom-header is a literal string, not a CFN
    dynamic reference. Background: warlordofmars/channel#5 — CFN does not
    reliably re-resolve {{resolve:ssm:}} for CloudFront origin custom-headers
    on subsequent deploys after an SSM ``put-parameter --overwrite``.

    The SSM parameter is created by the stack itself with a
    ``CHANGE_ME_ON_FIRST_DEPLOY`` placeholder. On a *first* deploy to a new
    env, that placeholder is still in SSM and gets read here — the stack
    then deploys with the same placeholder in the CloudFront origin header,
    which is fine until the operator overwrites the SSM value and re-deploys.
    """
    return ctx.run(
        f"aws ssm get-parameter --name /channel/{env}/origin-verify-secret"
        " --query Parameter.Value --output text",
        hide=True,
        warn=True,
    ).stdout.strip() or "CHANGE_ME_ON_FIRST_DEPLOY"


def _hosted_zone_id(ctx, zone_name: str = "warlordofmars.net") -> str:
    """Resolve the Route53 hosted zone ID.

    Checks HOSTED_ZONE_ID env var first; falls back to a Route53 API lookup.
    """
    if zone_id := os.environ.get("HOSTED_ZONE_ID"):
        return zone_id
    return (
        ctx.run(
            f"aws route53 list-hosted-zones-by-name --dns-name {zone_name}"
            " --query 'HostedZones[0].Id' --output text",
            hide=True,
        )
        .stdout.strip()
        .split("/")[-1]
    )


def _cfn_output(ctx, key, env="prod"):
    stack = _stack_name(env)
    return ctx.run(
        f"aws cloudformation describe-stacks --stack-name {stack} --region {REGION}"
        f" --query \"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue\""
        " --output text",
        hide=True,
    ).stdout.strip()


def _wait_for_http(url: str, label: str, timeout: int = 30) -> bool:
    """Poll url until it responds or timeout (seconds) elapses. Returns True on success."""
    for _ in range(timeout):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(1)
    print(f"  {label} did not start in time")
    return False


def _find_vite_port() -> int | None:
    """Scan ports 5173-5179 to find the Channel Vite dev server.

    Identifies the dev server by probing /auth/login?test_email=probe — only the
    Starter API (via Vite proxy) responds with the bypass HTML.  Other projects
    on the same port range won't have this endpoint.
    """
    for port in range(5173, 5180):
        try:
            url = f"http://localhost:{port}/auth/login?test_email=probe"
            resp = urllib.request.urlopen(url, timeout=2)
            body = resp.read(512).decode("utf-8", errors="ignore")
            if "localStorage.setItem" in body:
                return port
        except Exception:
            pass
    return None


# ── Lint ──────────────────────────────────────────────────────────────────────


@task
def lint_backend(ctx):
    """Lint backend Python with ruff (check + format)"""
    ctx.run("uv run ruff check src tests", pty=True)
    ctx.run("uv run ruff format --check src tests", pty=True)


@task
def lint_frontend(ctx):
    """Lint frontend with ESLint"""
    with ctx.cd(UI):
        ctx.run("npm run lint", pty=True)


@task
def lint_infra(ctx):
    """Lint CDK infra with ruff"""
    ctx.run("uv run ruff check infra", pty=True)


@task
def typecheck(ctx):
    """Type-check backend with mypy"""
    ctx.run("uv run mypy src/channel", pty=True)


@task
def check_copyright(ctx):
    """Check all source files have a copyright header"""
    ctx.run("uv run python scripts/check_copyright.py", pty=True)


@task(lint_backend, lint_frontend, lint_infra, typecheck, check_copyright)
def lint(ctx):
    """Lint + typecheck everything (backend + frontend + infra)"""


@task
def fmt(ctx):
    """Auto-format Python source with ruff"""
    ctx.run("uv run ruff format src tests", pty=True)
    ctx.run("uv run ruff check --fix src tests", pty=True)


# ── Audit ─────────────────────────────────────────────────────────────────────


@task
def audit_backend(ctx):
    """Security audit backend dependencies (pip-audit)"""
    ctx.run("uv run pip-audit --skip-editable", pty=True)


@task
def audit_frontend(ctx):
    """Security audit frontend dependencies (npm audit)"""
    with ctx.cd(UI):
        ctx.run("npm audit --audit-level=high", pty=True)


@task(audit_backend, audit_frontend)
def audit(ctx):
    """Audit all dependencies (backend + frontend)"""


# ── Test ──────────────────────────────────────────────────────────────────────


@task
def test_unit(ctx):
    """Run unit tests (no external deps)"""
    ctx.run("uv run pytest tests/unit -v", pty=True)


@task
def test_integration(ctx):
    """Run integration tests (requires DynamoDB Local on port 8000)"""
    env = {
        "DYNAMODB_ENDPOINT": f"http://localhost:{DYNAMO_PORT}",
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "AWS_DEFAULT_REGION": "us-east-1",
        "STARTER_JWT_SECRET": "test-secret",
    }
    ctx.run("uv run pytest tests/integration -v", env=env, pty=True)


@task
def test_frontend(ctx):
    """Run frontend vitest tests"""
    ci = bool(os.environ.get("CI"))
    extra = " -- --reporter=verbose" if ci else ""
    with ctx.cd(UI):
        ctx.run(f"npm test{extra}", pty=not ci)


@task(help={"coverage": "Run the v8 coverage gate (100% on main/* + preload/*)"})
def desktop_test(ctx, coverage=False):
    """Run desktop main-process unit tests."""
    cmd = "npm run test:coverage" if coverage else "npm test"
    ctx.run(f"cd {DESKTOP} && {cmd}", pty=True)


@task(
    help={
        "platform": "mac | win | linux | current",
        "api_base": "VITE_API_BASE for the SPA build",
    }
)
def desktop_build(ctx, platform="current", api_base="https://channel.warlordofmars.net"):
    """Build the Electron desktop app for the named platform.

    Outputs unsigned artifacts in desktop/release/. Signing + auto-update are
    sub-project B (see docs/superpowers/specs/2026-05-30-electron-shell-oauth-design.md).
    """
    # Build SPA with the Electron-specific API base URL
    ctx.run(f"cd {UI} && VITE_API_BASE={api_base} npm run build", pty=True)
    # Bundle main + preload via esbuild
    ctx.run(f"cd {DESKTOP} && npm run build:main", pty=True)
    # Copy SPA bundle into desktop/dist-renderer/
    ctx.run(f"cd {DESKTOP} && npm run build:renderer", pty=True)
    # Package for the target platform via electron-builder
    flag = "" if platform == "current" else f"--{platform}"
    ctx.run(f"cd {DESKTOP} && npx electron-builder {flag}", pty=True)


@task
def desktop_dev(ctx):
    """Start FastAPI + Vite + Electron pointing at localhost (Ctrl-C tears down all three)."""
    # Reuse the existing inv dev orchestration (DynamoDB Local + FastAPI + Vite)
    # and launch Electron alongside it. The Vite proxy in ui/vite.config.js
    # handles /auth + /api + /oauth + /mcp routes to FastAPI on :8001.
    env = os.environ.copy()
    env["VITE_DEV_SERVER_URL"] = f"http://localhost:{UI_PORT}"
    env["CHANNEL_API_BASE"] = f"http://localhost:{API_PORT}"

    # Rebuild the main+preload bundle so source edits to desktop/main/
    # or desktop/preload/ are reflected at launch (Electron loads the
    # bundled CJS output, not source).
    ctx.run(f"cd {DESKTOP} && npm run build:main", pty=True)

    api_proc = subprocess.Popen(["uv", "run", "inv", "dev"], cwd=ROOT)

    # Wait for Vite to be reachable before launching Electron
    vite_url = env["VITE_DEV_SERVER_URL"]
    for _ in range(60):
        try:
            urllib.request.urlopen(vite_url, timeout=1)
            break
        except Exception:
            time.sleep(1)

    electron_proc = subprocess.Popen(["npm", "run", "dev:electron"], cwd=DESKTOP, env=env)

    try:
        electron_proc.wait()
    finally:
        api_proc.send_signal(signal.SIGINT)
        api_proc.wait()


@task(test_unit, test_integration, test_frontend)
def test(ctx):
    """Run all tests (unit + integration + frontend)"""


@task(lint_backend, typecheck, check_copyright, test_unit, test_frontend, desktop_test)
def pre_push(ctx):
    """Local CI gate: lint + typecheck + copyright check + unit tests + frontend tests + desktop tests (run before every push)"""


@task
def e2e(ctx, env="prod"):
    """Run e2e tests against the deployed stack. Fetches URLs from CloudFormation."""
    api_url = _cfn_output(ctx, "ApiFunctionUrl", env=env)
    ui_url = _cfn_output(ctx, "UiUrl", env=env)
    extra_env = {
        "STARTER_API_URL": api_url,
        "STARTER_UI_URL": ui_url,
    }
    ctx.run(
        "uv run pytest tests/e2e -v",
        env=extra_env,
        pty=True,
    )


@task
def e2e_local(ctx, tests="tests/e2e", n=1):
    """Run e2e tests against the local dev stack (inv dev must already be running).

    Automatically detects the Vite port — no env vars to set manually.
    Pass --n=N to run the suite N times (useful for flakiness detection).

    test_docs_e2e.py is excluded by default — it requires a deployed VitePress
    build which is not served in the local dev stack.
    """
    api_url = f"http://localhost:{API_PORT}"
    if not _wait_for_http(f"{api_url}/health", "API", timeout=3):
        print(f"ERROR: API not responding at {api_url} — is 'inv dev' running?")
        sys.exit(1)
    vite_port = _find_vite_port()
    if not vite_port:
        print("ERROR: Could not find Channel Vite dev server on ports 5173-5179")
        print("       Make sure 'inv dev' is running and the UI has started.")
        sys.exit(1)
    ui_url = f"http://localhost:{vite_port}"
    print(f"  API: {api_url}")
    print(f"  UI:  {ui_url}")
    extra_env = {
        **os.environ,
        "STARTER_API_URL": api_url,
        "STARTER_UI_URL": ui_url,
    }
    # Docs tests require a deployed VitePress build — skip unless explicitly targeted
    ignore = " --ignore=tests/e2e/test_docs_e2e.py" if tests == "tests/e2e" else ""
    for i in range(n):
        if n > 1:
            print(f"\n--- run {i + 1}/{n} ---")
        ctx.run(f"uv run pytest {tests}{ignore} -v", env=extra_env, pty=True)


# ── Local dev ─────────────────────────────────────────────────────────────────


@task
def dynamo_start(ctx):
    """Start DynamoDB Local in Docker (detached)"""
    ctx.run(
        f"docker run -d --name {DYNAMO_CONTAINER} -p {DYNAMO_PORT}:{DYNAMO_PORT}"
        " amazon/dynamodb-local:latest",
        warn=True,
        hide=True,
    )
    print(f"DynamoDB Local running on port {DYNAMO_PORT}")


@task
def dynamo_stop(ctx):
    """Stop and remove the DynamoDB Local container"""
    ctx.run(f"docker rm -f {DYNAMO_CONTAINER}", warn=True, hide=True)
    print("DynamoDB Local stopped")


@task
def dev(ctx, seed=False):
    """Start DynamoDB Local + management API + UI dev server (Ctrl-C to stop all).

    Pass --seed to automatically seed demo data once the API is ready.
    """
    jwt_secret = os.environ.get("STARTER_JWT_SECRET", "dev-secret")
    # Allow all localhost Vite ports (5173–5179) so CORS doesn't break when
    # 5173 is already occupied by another project and Vite picks the next port.
    cors_origins = ",".join(f"http://localhost:{p}" for p in range(5173, 5180))
    # DDB Local accepts any credentials (including those from
    # ~/.aws/credentials), so we don't override AWS_ACCESS_KEY_ID /
    # AWS_SECRET_ACCESS_KEY. That lets Bedrock-backed endpoints
    # authenticate against real AWS using the developer's profile,
    # while DDB Local still works through the DYNAMODB_ENDPOINT override.
    # If the developer has NO AWS credentials at all, boto3 will raise
    # NoCredentialsError on the first DDB call; in that case set
    # AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY to anything before
    # running `inv dev`.
    dev_env = {
        **os.environ,
        "STARTER_JWT_SECRET": jwt_secret,
        "STARTER_TABLE_NAME": "channel",
        "DYNAMODB_ENDPOINT": f"http://localhost:{DYNAMO_PORT}",
        "AWS_DEFAULT_REGION": "us-east-1",
        "CORS_ORIGINS": cors_origins,
        # Prevents VectorStore instantiation from crashing on every request;
        # semantic search will still fail locally (no real S3 Vectors bucket).
        # No vector store in channel,
        # Always enable auth bypass in local dev — the bypass only activates when
        # ?test_email= is present, so normal browser flows are unaffected.
        "STARTER_BYPASS_GOOGLE_AUTH": "1",
        # Phase 7c: mount /api/_debug/* so the Playwright e2e can verify
        # AgentCore writes. Prod stacks do NOT set this — see
        # tests/unit/test_channel_stack.py for the guard.
        "STARTER_ENABLE_DEBUG_ENDPOINTS": "1",
        # Phase 7c: AgentCore Memory writes need an env tag for the
        # ``channel-{env}`` naming convention. Personal dev points at
        # the developer's own AWS account, so default to "jc"; override
        # via STARTER_AGENTCORE_MEMORY_NAME if pointing at a shared
        # pre-existing Memory.
        "STARTER_ENV": os.environ.get("STARTER_ENV", "jc"),
    }
    ui_env = {
        **os.environ,
        "VITE_API_BASE": f"http://localhost:{API_PORT}",
    }

    # Start DynamoDB Local
    subprocess.run(
        ["docker", "rm", "-f", DYNAMO_CONTAINER],
        capture_output=True,
    )
    dynamo_proc = subprocess.Popen(
        [
            "docker",
            "run",
            "--name",
            DYNAMO_CONTAINER,
            "-p",
            f"{DYNAMO_PORT}:{DYNAMO_PORT}",
            "amazon/dynamodb-local:latest",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Start management API
    api_proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "channel.api.main:app", f"--port={API_PORT}", "--reload"],
        cwd=ROOT,
        env=dev_env,
    )

    # Start UI dev server
    ui_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=UI,
        env=ui_env,
    )

    procs = [dynamo_proc, api_proc, ui_proc]

    def _shutdown(sig, frame):
        print("\nShutting down...")
        for p in procs:
            p.terminate()
        subprocess.run(["docker", "rm", "-f", DYNAMO_CONTAINER], capture_output=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Detect the actual Vite port (may differ from UI_PORT if that port is taken)
    _wait_for_http(f"http://localhost:{API_PORT}/health", "API", timeout=20)
    actual_ui_port = _find_vite_port() or UI_PORT

    print("\nServices starting:")
    print(f"  DynamoDB Local → http://localhost:{DYNAMO_PORT}")
    print(f"  Management API  → http://localhost:{API_PORT}")
    print(f"  UI dev server   → http://localhost:{actual_ui_port}")
    print()

    if seed:
        print("--seed is not implemented — add your own seed script.")
    print("Press Ctrl-C to stop all services.\n")

    for p in procs:
        p.wait()


# ── CDK ───────────────────────────────────────────────────────────────────────


@task
def export_openapi(ctx, out="docs-site/public/openapi.json"):
    """Export the FastAPI management-API OpenAPI spec to a static file (#421).

    The docs site renders the spec via Scalar from ``openapi.json``; the
    CI ``openapi-spec-check`` job re-runs this task and fails if the
    committed file has drifted from the live schema. Re-run after
    changing any ``@router.*`` signature, summary, or response model.

    ``info.version`` is normalised to ``"dev"`` so the committed spec is
    stable across environments — the installed channel package version
    varies by build (``setuptools_scm`` appends the git sha + date) and
    would otherwise trip the drift check on every commit.
    """
    import json
    from pathlib import Path

    # Import lazily so `inv --help` doesn't need the full app tree on sys.path.
    from channel.api.main import app

    spec = app.openapi()
    spec.setdefault("info", {})["version"] = "dev"
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out_path} ({len(json.dumps(spec))} bytes)")


@task
def synth(ctx, env="prod"):
    """Synthesize CDK template locally (skips Docker bundling). Use --env dev for dev stack."""
    account = _aws_account(ctx)
    zone_id = _hosted_zone_id(ctx)
    origin_verify = _origin_verify_secret(ctx, env)
    stack = _stack_name(env)
    with ctx.cd(INFRA):
        ctx.run(
            f"uv run cdk synth {stack} --no-staging"
            f" -c account={account} -c env={env} -c hosted_zone_id={zone_id}"
            f" -c origin_verify_secret={origin_verify}",
            pty=True,
        )


@task
def diff(ctx, env="prod"):
    """Show CDK diff against the deployed stack. Use --env dev for dev stack."""
    account = _aws_account(ctx)
    zone_id = _hosted_zone_id(ctx)
    origin_verify = _origin_verify_secret(ctx, env)
    stack = _stack_name(env)
    with ctx.cd(INFRA):
        ctx.run(
            f"uv run cdk diff {stack}"
            f" -c account={account} -c env={env} -c hosted_zone_id={zone_id}"
            f" -c origin_verify_secret={origin_verify}",
            pty=True,
        )


@task
def deploy(ctx, env="prod"):
    """Deploy CDK stack to AWS. Use --env dev for dev stack."""
    account = _aws_account(ctx)
    zone_id = _hosted_zone_id(ctx)
    origin_verify = _origin_verify_secret(ctx, env)
    stack = _stack_name(env)
    if env == "prod":
        # In CI, APP_VERSION is set by the release job. Locally, infer from commits.
        app_version = os.environ.get("APP_VERSION", _infer_next_version(ctx))
    else:
        short_sha = ctx.run("git rev-parse --short HEAD", hide=True).stdout.strip()
        app_version = f"{_infer_next_version(ctx)}-{env}.{short_sha}"

    # Build the React UI so assets are included in the S3 deployment.
    # CI does this explicitly before cdk deploy; local deploys must do the same.
    with ctx.cd(UI):
        ctx.run("npm install --silent", hide=True)
        ctx.run("npm run build", pty=True)

    with ctx.cd(INFRA):
        ctx.run(
            f"uv run cdk deploy {stack} --require-approval never"
            f" -c account={account} -c env={env} -c hosted_zone_id={zone_id}"
            f" -c origin_verify_secret={origin_verify}",
            env={"APP_VERSION": app_version},
            pty=True,
        )


@task
def outputs(ctx, env="prod"):
    """Print CloudFormation stack outputs. Use --env dev for dev stack."""
    stack = _stack_name(env)
    ctx.run(
        f"aws cloudformation describe-stacks --stack-name {stack}"
        f" --region {REGION}"
        " --query 'Stacks[0].Outputs' --output table --no-cli-pager",
        pty=True,
    )


# ── Lambda logs ───────────────────────────────────────────────────────────────


def _lambda_name(ctx, logical_id: str, env: str = "prod") -> str:
    """Look up the physical Lambda function name from the CloudFormation stack."""
    stack = _stack_name(env)
    return ctx.run(
        f"aws cloudformation describe-stack-resources --stack-name {stack}"
        f" --logical-resource-id {logical_id} --region {REGION}"
        " --query 'StackResources[0].PhysicalResourceId' --output text",
        hide=True,
    ).stdout.strip()


@task
def logs_api(ctx, env="prod"):
    """Tail management API Lambda CloudWatch logs (Ctrl-C to stop)."""
    fn_name = _lambda_name(ctx, "ApiFunction", env=env)
    ctx.run(f"aws logs tail /aws/lambda/{fn_name} --follow --region {REGION}", pty=True)


# ── Release ───────────────────────────────────────────────────────────────────


@task
def version(ctx):
    """Print the next semantic version inferred from conventional commits."""
    print(_infer_next_version(ctx))


@task
def back_merge(ctx):
    """Open a PR to merge main back into development after a prod release (auto-merges)."""
    # Check if main has commits not in development — nothing to do if branches are identical.
    ahead = ctx.run(
        "git fetch origin main development --quiet"
        " && git rev-list --count origin/development..origin/main",
        hide=True,
        warn=True,
    ).stdout.strip()
    if ahead == "0":
        print("main and development are already in sync — nothing to back-merge")
        return

    # Check if a PR already exists.
    existing = ctx.run(
        "gh pr list --base development --head main --state open --json number --jq '.[0].number'",
        hide=True,
        warn=True,
    ).stdout.strip()
    if existing:
        print(f"Back-merge PR #{existing} already open — enabling auto-merge")
        ctx.run(f"gh pr merge '{existing}' --auto --merge", warn=True)
        return

    result = ctx.run(
        "gh pr create"
        " --base development"
        " --head main"
        " --title 'chore: merge main back to development'"
        " --body 'Back-merge after prod release. Merge using **merge commit** (not squash).'",
        warn=True,
    )
    if result.ok:
        pr_url = result.stdout.strip().splitlines()[-1]
        print(f"PR created: {pr_url}")
        ctx.run(f"gh pr merge '{pr_url}' --auto --merge", warn=True)
    else:
        print(f"gh pr create failed: {result.stderr.strip()}")


# ── Hooks ─────────────────────────────────────────────────────────────────────


@task
def install_hooks(ctx):
    """Install git hooks from hooks/ into .git/hooks/ (run once after cloning)"""
    hooks_src = ROOT / "hooks"
    hooks_dst = ROOT / ".git" / "hooks"
    for hook in hooks_src.iterdir():
        dst = hooks_dst / hook.name
        dst.unlink(missing_ok=True)
        dst.symlink_to(hook.resolve())
        dst.chmod(0o755)
        print(f"  Installed {hook.name} → .git/hooks/{hook.name}")
    print("Git hooks installed.")


# ── Clean ─────────────────────────────────────────────────────────────────────


@task
def clean(ctx):
    """Remove build artifacts (cdk.out, ui/dist, __pycache__, .pytest_cache, .mypy_cache)"""
    ctx.run(
        "find . -path ./.venv -prune -o -type d -name __pycache__ -print -exec rm -rf {} + 2>/dev/null; true"
    )
    ctx.run("rm -rf infra/cdk.out ui/dist .pytest_cache .mypy_cache .ruff_cache")
    print("Clean.")
