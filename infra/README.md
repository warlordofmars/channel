# Infrastructure

AWS CDK (Python) stack that provisions all Channel resources. Defined in `stacks/starter_stack.py`.

## Resources created

| Resource | Name / ID | Notes |
|---|---|---|
| DynamoDB table | `channel-{env}` | Single-table, PAY_PER_REQUEST, PITR enabled (prod), TTL on `ttl` attribute |
| DynamoDB GSI | `KeyIndex` | `GSI1PK` + `GSI1SK` — key lookups |
| DynamoDB GSI | `TagIndex` | `GSI2PK` + `GSI2SK` — list by tag |
| Lambda | `ApiFunction` | FastAPI management API, Python 3.12, 512 MB, 30s timeout |
| Lambda Function URL | (API) | `auth=NONE`, CORS open, HTTPS only |
| S3 Bucket | `UiBucket` | Private, OAC, auto-delete on stack removal |
| CloudFront Distribution | `UiDistribution` | UI from S3, `/api/*` + `/auth/*` → API Lambda |
| SSM Parameter | `/channel/jwt-secret` | JWT signing secret, `RETAIN` policy |
| IAM Role | `ApiLambdaRole` | DynamoDB + SSM read, Lambda basic execution |

### CloudFront routing

| Path | Origin |
|---|---|
| `/*` (default) | S3 bucket (React UI) |
| `/api/*` | API Lambda Function URL |
| `/auth/*` | API Lambda Function URL |
| `/health` | API Lambda Function URL |

### Stack outputs

| Output | Description |
|---|---|
| `ChannelStack.ApiFunctionUrl` | Direct API Lambda URL |
| `ChannelStack.UiUrl` | CloudFront URL (use for admin UI + API) |
| `ChannelStack.TableName` | DynamoDB table name |

## Lambda bundling

The Lambda package is built inside a Docker container (the Lambda Python 3.12 build image) during CDK synthesis:

1. Install `uv` via pip
2. `uv export --no-group dev --no-group infra` → `/tmp/requirements.txt` (runtime deps only)
3. `pip install -r /tmp/requirements.txt -t /asset-output`
4. `cp -r src/starter /asset-output/starter`

The `dev` and `infra` dependency groups are excluded to keep the Lambda package under the 250 MB limit.

## Initial deployment

### Prerequisites

- AWS CLI configured with appropriate credentials
- Node.js 20+ (for CDK CLI)
- Docker (for Lambda bundling)
- `uv` installed

```bash
# Install CDK CLI
npm install -g aws-cdk

# Install infra dependencies
uv sync --group dev --group infra

# Bootstrap CDK (first time only, per account/region)
cd infra
cdk bootstrap -c account=YOUR_ACCOUNT_ID -c env=dev

# Build the UI first (CDK uploads it during deploy)
cd ../ui && npm install && npm run build && cd ../infra

# Deploy
uv run inv deploy --env dev
```

On first deploy, rotate the JWT secret from the placeholder value:

```bash
aws ssm put-parameter \
  --name /channel/jwt-secret \
  --value "$(openssl rand -hex 32)" \
  --overwrite
```

## CI/CD deployment (GitHub Actions)

Subsequent deployments happen automatically on push to `main`. See [../.github/workflows/ci.yml](../.github/workflows/ci.yml).

### Required GitHub secrets

| Secret | Description |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | ARN of the OIDC IAM role for GitHub Actions |

### OIDC IAM role

The deploy job assumes an IAM role via OIDC (no long-lived access keys). The trust policy should be scoped to your repo.

Required IAM permissions: CloudFormation, S3, IAM, Lambda, DynamoDB, SSM, ECR (for bundling image pull), STS.

## Useful CDK commands

```bash
cd infra

# Show what will change before deploying
cdk diff -c account=YOUR_ACCOUNT_ID -c env=dev

# Deploy without approval prompts
cdk deploy -c account=YOUR_ACCOUNT_ID -c env=dev --require-approval never

# Synthesize CloudFormation template
cdk synth -c account=YOUR_ACCOUNT_ID -c env=dev

# Destroy the stack (DynamoDB table and SSM parameter are RETAINED)
cdk destroy -c account=YOUR_ACCOUNT_ID -c env=dev
```

## Configuration

All Lambda configuration is via environment variables set in the CDK stack:

| Variable | Set by | Description |
|---|---|---|
| `STARTER_TABLE_NAME` | CDK | DynamoDB table name |
| `STARTER_ISSUER` | CDK | JWT issuer URL |
| `STARTER_JWT_SECRET_PARAM` | (optional) | SSM parameter name for JWT secret (defaults to `/channel/jwt-secret`) |
| `DYNAMODB_ENDPOINT` | (local only) | Override DynamoDB endpoint for local development |
