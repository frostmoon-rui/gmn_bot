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
        self.speech_failures = 0
        self.speech_retry_at = 0.0

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

    async def decide(self, event, text, addressed, *, legacy=None):
        if not addressed or self.clock() < self.retry_at:
            return None
        try:
            text = self.normalize(text.strip())
            if not re.match(r"^/?autonomy(?:\s|$)", text, re.I):
                return None
            decision = await self.adapter.on_incoming_event(
                event, text, addressed, learn=self.learning_allowed(event, legacy))
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

    @staticmethod
    def learning_allowed(event, legacy):
        if legacy is None:
            return False
        profile = legacy.social.profile(event)
        if event.get("message_type") == "group":
            return not profile.get("disabled")
        return bool(profile.get("enabled"))

    def _speech_error(self, error):
        self.speech_failures += 1
        if self.speech_failures >= 2:
            self.speech_retry_at = self.clock() + 60
        LOG.warning("Autonomy conversation fell back (%s)", type(error).__name__)

    async def prepare(self, event, text, recent, *, passive, addressed, legacy):
        if self.clock() < self.speech_retry_at:
            return None
        try:
            async with asyncio.timeout(1.6):
                plan = await self.adapter.prepare(
                    event, text, recent, passive=passive, addressed=addressed,
                    learn=self.learning_allowed(event, legacy))
            return plan
        except Exception as error:
            self._speech_error(error)
            return None

    def bubbles(self, text, plan=None, *, group=False, passive=False, request=""):
        if self.clock() < self.speech_retry_at or (plan is not None and not plan.format_text):
            return None
        try:
            from gmn_autonomy.core.conversation import HELP, SERIOUS, phase, render

            if request and (SERIOUS.search(request) or HELP.search(request)):
                return None
            bubbles = render(text, group=group, passive=passive, phase_name=plan.phase if plan else phase(time.time()),
                             bubble_limit=plan.bubble_limit if plan else None)
            self.speech_failures = 0
            return bubbles
        except Exception as error:
            self._speech_error(error)
            return None

    def is_dialogue(self, text):
        return bool(re.match(r"^/?autonomy\s+chat(?:[\s:\uFF1A]|$)", self.normalize(text.strip()), re.I))

    def note_room(self, event, text):
        if self.clock() < self.speech_retry_at:
            return
        try:
            self.adapter.note_room(event, text)
        except Exception as error:
            self._speech_error(error)

    def observe(self, event, text, answer, addressed, legacy):
        try:
            self.adapter.observe(event, text, answer, addressed, self.learning_allowed(event, legacy))
        except Exception as error:
            self._speech_error(error)

    def privacy_notice(self):
        return self.adapter.runtime.messages["group_privacy"]

    async def forget(self, event):
        try:
            await self.adapter.forget(event)
        except Exception as error:
            LOG.warning("Autonomy privacy change could not complete (%s)", type(error).__name__)

    async def review_loop(self):
        while True:
            await asyncio.sleep(15)
            try:
                await self.adapter.refresh_review()
            except Exception as error:
                LOG.warning("Autonomy conversation review failed (%s)", type(error).__name__)

    async def heartbeat(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.adapter.heartbeat()
            except Exception as error:
                LOG.warning("Autonomy heartbeat failed (%s)", type(error).__name__)
