# Alarm delivery

The stack creates 24 CloudWatch alarms and one SNS topic
(`AlarmTopic`) for them to publish to. An alarm that fires into a topic
with no subscriber notifies nobody, so alarm *delivery* is a separate
thing from alarm *correctness* and has its own control.

Two guards exist and they cover different halves of the chain:

| Guard | What it proves | Where |
| --- | --- | --- |
| `test_every_channel_namespace_alarm_references_an_emitted_metric` | Each alarm watches a metric the code really publishes | `tests/unit/test_channel_stack.py` |
| **Verify prod alarm subscription** | The topic can reach a human | `.github/workflows/ci.yml`, post-deploy |

Before the second one existed, the pipeline read: metric emitted
correctly → alarm wired correctly → topic with zero subscribers →
silence (#537).

## The posture: prod is enforced, dev and jc are silent on purpose

Decided 2026-08-08 on #537.

- **`prod`** must have at least one **confirmed** subscription on its
  alarm topic. The CI job below fails the release pipeline if it does
  not.
- **`dev` and personal stacks (`jc`)** get no gate. Their alarms notify
  nobody, and that is a recorded decision rather than an oversight: dev
  is where the work happens and zero friction on personal stacks was
  judged worth the cost. The accepted consequence is that a real dev
  outage pages nobody — the signal is a CloudWatch graph somebody
  thinks to look at.
- The per-cold-start `WARNING` about the `alarm-email` SSM placeholder
  is a **reminder, not a control**. It logged 109 times in 7 days
  without anything changing, which is what a reminder does when the
  person who needs it is not reading that log. Nothing should be built
  on top of it.

## Confirmed, not merely present

This is the part worth being precise about.

An email subscription is not live the moment you create it. SNS sends a
confirmation link to the address and the subscription stays inert until
someone clicks it. In that state:

```console
$ aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN"
{
  "Subscriptions": [
    {
      "SubscriptionArn": "PendingConfirmation",
      "Protocol": "email",
      "Endpoint": "alerts@example.com",
      ...
    }
  ]
}
```

`SubscriptionArn` reads the literal string `PendingConfirmation`
instead of an ARN. The subscription **record** exists; delivery does
not. A check that asserted only "the topic has a subscription" would
pass here — in exactly the broken state the control exists to catch.

So the check asks *is this a real ARN* (an allowlist), not *is this
string something other than `PendingConfirmation`* (a denylist). A
denylist would also admit `Deleted`, which SNS returns the same way,
and whatever sentinel it adds next.

## Why the check is post-deploy, not a synth assertion

Confirmation state does not exist at synth time. A CloudFormation
template can declare a subscription; it cannot record whether the
recipient ever clicked the link. `PendingConfirmation` is visible only
by querying live SNS. A synth-time template assertion is therefore
structurally incapable of asserting the thing that matters — the most
it could prove is that a subscription was *declared*, which is the
weaker claim that passes in the broken state.

Implementation: `require_confirmed_alarm_subscription` in
`infra/stacks/channel_stack.py`, run from the
**Verify prod alarm subscription** job after `deploy-prod`. The job
resolves the topic from the stack's `AlarmTopicArn` CloudFormation
output, walks `ListSubscriptionsByTopic`, and exits non-zero unless at
least one subscription carries a real ARN. It also posts to Slack on
failure.

The environment gate lives inside the function as well as in the job's
`if:` condition, so a non-prod invocation makes no AWS call at all —
not even constructing a client.

## First-deploy checklist (prod)

The first prod deploy will fail this gate, by design: the topic is
created empty and nobody is subscribed yet. The stack deploy itself
succeeds; what fails is the assertion that prod is ready to be
considered live.

1. **Deploy.** `AlarmTopic` and the `/channel/prod/alarm-email` SSM
   parameter are created, the latter holding
   `CHANGE_ME_ON_FIRST_DEPLOY`.

2. **Record the recipient** in SSM. This is documentation of who owns
   alarm response; it is not itself the delivery path.

   ```bash
   aws ssm put-parameter --name /channel/prod/alarm-email \
     --value 'alerts@example.com' --type String --overwrite
   ```

3. **Subscribe the address** to the topic.

   ```bash
   TOPIC_ARN=$(aws cloudformation describe-stacks --stack-name ChannelStack \
     --region us-east-1 \
     --query "Stacks[0].Outputs[?OutputKey=='AlarmTopicArn'].OutputValue" \
     --output text)

   aws sns subscribe --topic-arn "$TOPIC_ARN" \
     --protocol email --notification-endpoint 'alerts@example.com'
   ```

4. **Confirm it from the inbox.** Click the link in the
   "AWS Notification - Subscription Confirmation" email. Skipping this
   step is the whole failure mode — everything looks configured and
   nothing is delivered.

5. **Verify**, and expect a real ARN rather than `PendingConfirmation`:

   ```bash
   aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
     --query "Subscriptions[].SubscriptionArn"
   ```

6. **Re-run** the failed *Verify prod alarm subscription* job. It
   should now report the confirmed subscription count and pass.

Any protocol SNS supports works — a chatbot or PagerDuty HTTPS endpoint
confirms automatically and satisfies the gate the same way. Email is
simply the lowest-setup option.

## When the gate fails on a later release

It means somebody unsubscribed, the address bounced hard enough for SNS
to disable it, or the topic was replaced. Alarms have been silent since
whenever that happened. Work back through the checklist from step 3.
