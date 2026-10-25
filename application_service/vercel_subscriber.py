"""Private Vercel Queues subscriber. No workstation process is required."""
import os
from vercel.queue import Message, RetryAfter, subscribe

from .vercel_workflows import Engine, Busy, ConfigurationRequired


@subscribe(topic='ajss-workflows', consumer_group='ajss-python-workflows', max_concurrency=1, retry_after=60)
def consume(message: Message[dict[str, str]]) -> None:
    try:
        if os.environ.get('AJSS_VERCEL_WORKFLOWS_ENABLED') != '1':
            raise ConfigurationRequired('Hosted execution is not activated')
        payload = message.payload
        unit_id = payload['unit_id']
        # Daily wakeups use a new queue key but the same durable unit plan.
        if unit_id.startswith('admit:'):
            unit_id = 'admit'
        Engine().consume(payload['run_id'], unit_id)
    except (Busy, ConfigurationRequired):
        raise RetryAfter(300) from None
