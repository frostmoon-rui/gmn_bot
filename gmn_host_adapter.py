"""Optional boundary between GMN Bot and the independently released runtime."""
import asyncio
import logging
import re
import time

from bot_actions import parse_action

LOG = logging.getLogger(__name__)
KNOWN_TOOLS = frozenset({"help", "status", "sources", "earthquake"})


def enabled(value):
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


class AutonomyBridge:
    def __init__(self, adapter, *, normalize=None, clock=time.monotonic):
        self.adapter = adapter
        self.normalize = normalize or (lambda text: text)
        self.clock = clock
        self.failures = 0
        self.retry_at = 0.0

    @classmethod
    async def open(cls, switch, database, model, *, normalize=None, **options):
        if not enabled(switch):
            return None
        try:
            from gmn_autonomy import ActiveLearningRuntime
            from gmn_autonomy.adapters.gmn_host_adapter import GmnHostAdapter

            runtime = await ActiveLearningRuntime.open(database, model, **options)
            return cls(GmnHostAdapter(runtime), normalize=normalize)
        except Exception as error:
            LOG.warning("Autonomy unavailable at startup (%s); continuing with the host", type(error).__name__)
            return None

    async def decide(self, event, text, addressed):
        if not addressed or self.clock() < self.retry_at:
            return None
        try:
            text = self.normalize(text.strip())
            if not re.match(r"^/?autonomy\s", text, re.I):
                return None
            decision = await self.adapter.on_incoming_event(event, text, addressed)
            if decision is None:
                self.failures = 0
                return None
            if decision.kind == "tool" and decision.value in KNOWN_TOOLS:
                action = parse_action('[BOT_ACTION: {"tool":"'+decision.value+'","args":{}}]')
                if action is None:
                    raise ValueError("Invalid host action")
                result = ("tool", action)
            elif decision.kind == "reply" and isinstance(decision.value, str) and decision.value.strip():
                result = ("reply", decision.value)
            else:
                raise ValueError("Invalid autonomy decision")
            self.failures = 0
            return result
        except Exception as error:
            self.failures += 1
            if self.failures >= 2:
                self.retry_at = self.clock() + 60
            LOG.warning("Autonomy decision fell back to the host (%s)", type(error).__name__)
            return None

    async def heartbeat(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.adapter.heartbeat()
            except Exception as error:
                LOG.warning("Autonomy heartbeat failed (%s)", type(error).__name__)
