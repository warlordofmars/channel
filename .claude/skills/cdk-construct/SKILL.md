---
name: cdk-construct
description: "Conventions for authoring CDK constructs in the Channel stack (infra/stacks/channel_stack.py) — props pattern, cross-construct value exposure, IAM scoping, and CDK Nag suppression placement. Placeholder stub: conventions not yet codified into a full skill."
status: stub
triggers:
  paths:
    - "infra/stacks/**.py"
    - "infra/app.py"
  areas:
    - "infra"
---
> STUB — the infra is a single `ChannelStack`; the partitioned
> construct conventions are not yet codified. See §Gaps.

# cdk-construct

Skill scope: CDK construct conventions for the Channel stack —
props pattern, cross-construct value exposure, IAM scoping, and
CDK Nag suppression placement.

Today all infra lives in a single `ChannelStack(cdk.Stack)` class
at `infra/stacks/channel_stack.py` (wired from the CDK app entry
at `infra/app.py`). That file is the current reference for how
resources are wired, IAM grants are scoped, and CDK Nag
suppressions are attached. The stack has **not** been partitioned
into per-domain constructs, so a construct-authoring convention set
does not yet exist to document — new infra work should follow the
existing patterns in `channel_stack.py`.

## What we know now (intent only)

These are general CDK practices the future full skill is expected
to formalize; they are not yet mechanically enforced:

- **Each construct owns its own IAM scope.** Cross-construct grants
  happen at the composer (stack) level, not inside the construct
  that needs the grant. A construct never reaches into another
  construct's resource to attach a policy.
- **Constructs expose typed values to one another.** The exact
  shape of the props pattern (typed kwargs vs dataclass) is not yet
  settled — see §Gaps.
- **CDK Nag suppressions live with the construct that owns the
  suppressed resource.** A suppression on a Lambda role lives in
  the construct that defines that Lambda, not in the composer.

## Gaps

### Props convention not settled

- **What's missing:** Whether constructs accept typed `**kwargs`, a
  `@dataclass` props object, or a TypedDict — and the naming
  convention for the props type.
- **Why deferred:** No partitioned constructs exist yet
  (`channel_stack.py` is a single class); the convention only
  emerges when the stack is split.
- **Unblocks when:** a future partition of `channel_stack.py`
  establishes the props shape; no tracking issue is scheduled yet —
  file one per the README §"When to add a new skill" soft rule when
  the friction recurs.

### Public-attribute pattern for cross-construct exposure

- **What's missing:** The exact convention for how one construct
  exposes a value (table ARN, function URL, role) to another —
  attribute on the construct instance, accessor method, or
  re-exposed via stack-level outputs.
- **Why deferred:** The pattern only emerges once two or more
  constructs need to consume each other's outputs at the composer
  level.
- **Unblocks when:** the same partition work wires the first
  cross-construct consumer; no issue scheduled yet.

### Construct-level vs composer-level helpers

- **What's missing:** Whether per-resource invariant helpers belong
  on the construct that owns the resource or on the composer that
  assembles them.
- **Why deferred:** The boundary depends on whether the helper is a
  per-resource invariant (construct) or a stack-wide validation
  (composer); the partition is what draws the line.
- **Unblocks when:** the same partition places the first such
  helpers; no issue scheduled yet.

## See also

- `infra/stacks/channel_stack.py` — the current single-class
  `ChannelStack`; the reference for how resources, IAM grants, and
  CDK Nag suppressions are wired today.
- `infra/app.py` — the CDK app entry point.
- [ADR-0006](../../../docs/adr/0006-skills-system.md)
  §"Stub skill convention" — the schema contract this stub follows.
