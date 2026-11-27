"""Learn how much interruption or follow-up a conversation welcomes."""
import re

DAY = 86400
CHIME_WINDOW = 300
CARE_WINDOW = 2*DAY
CHIME_GAP = (300, 900, 1800, 3600)
GROUP_NEGATIVE = re.compile(r"(?:please )?(?:stop (?:chiming in|interrupting)|too (?:noisy|annoying|talkative)|don't interrupt)", re.I)
CARE_NEGATIVE = re.compile(r"(?:please )?(?:no more check-ins|stop (?:checking in|asking)|don't (?:check in|ask me again))", re.I)
CARE_SPACE = re.compile(r"(?:not now|I'd rather not talk about it|I don't want to talk about it)", re.I)
CARE_POSITIVE = re.compile(r"(?:thanks? for (?:asking|checking in)|I appreciate the check-in)", re.I)


def message_id(receipt):
    ident = str(receipt.get('message_id') or '') if isinstance(receipt, dict) else ''
    return ident if re.fullmatch(r'-?\d+', ident) else ''


class AttentionLearning:
    def __init__(self, owner, *, group_negative=None, care_negative=None, care_positive=None, care_space=None):
        self.owner = owner
        self.group_negative = group_negative or GROUP_NEGATIVE
        self.care_negative = care_negative or CARE_NEGATIVE
        self.care_positive = care_positive or CARE_POSITIVE
        self.care_space = care_space or CARE_SPACE

    @staticmethod
    def _key(event):
        return f"{int(event.get('self_id') or 0)}:attention:{int(event['group_id'])}:0"

    def _save(self):
        try:
            self.owner._save()
        except (OSError, RuntimeError) as error:
            print('[Attention learning storage]:', type(error).__name__)

    def _group(self, event, create=False):
        key = self._key(event)
        if create and key not in self.owner.state and len(self.owner.state) < 512:
            self.owner.state[key] = {'attention': {'updated': self.owner.clock()}}
        return self.owner.state.get(key, {}).get('attention')

    @staticmethod
    def _level(row, now):
        elapsed = max(0, now - row.get('updated', now))
        return max(0, row.get('level', 0) - int(elapsed // (7*DAY)))

    def _set_level(self, row, level, now):
        row.update(level=min(len(CHIME_GAP)-1, level), updated=now)

    def group_allowed(self, event):
        row = self._group(event)
        now = self.owner.clock()
        if not row:
            return True
        if now < row.get('pause_until', 0):
            return False
        return now - row.get('sent', 0) >= CHIME_GAP[self._level(row, now)]

    def chime_sent(self, event, receipt_id):
        if not receipt_id:
            return
        row = self._group(event, create=True)
        if row is None:
            return
        row.update(sent=self.owner.clock(), receipt=receipt_id, pending=True, turns=0)
        self._save()

    def group_feedback(self, event, text, *, quoted=None, addressed=False, complaint=False):
        clean = text.strip().rstrip('.!。！~～')
        negative = complaint or (addressed and bool(self.group_negative.fullmatch(clean)))
        row = self._group(event, create=negative)
        if row is None:
            return
        now = self.owner.clock()
        level = self._level(row, now)
        if negative:
            # Repeated complaints in one burst should not all increment the level.
            if now >= row.get('pause_until', 0):
                self._set_level(row, level + 2, now)
            row.update(pause_until=now + 1800, pending=False, turns=0)
            self._save()
            return True
        if not row.get('pending'):
            return
        if now - row['sent'] >= CHIME_WINDOW:
            self._settle_group(row, now)
            self._save()
        elif quoted and str(quoted) == row.get('receipt') and self.owner._plain(clean) and len(clean) >= 4:
            self._set_level(row, max(0, level - 1), now)
            row.update(pending=False, turns=0)
            self._save()
        elif clean:
            row['turns'] = min(3, row.get('turns', 0) + 1)

    def _settle_group(self, row, now):
        if row.get('turns', 0) >= 3:
            self._set_level(row, self._level(row, now) + 1, now)
        row.update(pending=False, turns=0)

    def maintain_group(self, record, now):
        row = record.get('attention')
        if not row:
            return False
        if now - max(row.get('updated', 0), row.get('sent', 0), row.get('pause_until', 0)) >= 30*DAY:
            record.pop('attention')
            return True
        if row.get('pending') and now - row['sent'] >= CHIME_WINDOW:
            self._settle_group(row, now)
            return True
        return False

    @staticmethod
    def care_allowed(social, now):
        care = social.get('care_attention', {})
        return not care.get('pending') and now >= care.get('until', 0)

    @staticmethod
    def reserve_care(social, now):
        social['care_attention'] = {'sent': now, 'until': now + 7*DAY}

    def care_sent(self, social, receipt):
        social['care_attention'].update(receipt=message_id(receipt), pending=True)
        self._save()

    def care_feedback(self, event, text, quoted=None):
        social = self.owner.social.profile(event)
        if not social.get('care_enabled'):
            return False
        self.maintain_care(social, self.owner.clock())
        row = social.get('care_attention', {})
        clean = text.strip().rstrip('.!。！~～')
        if self.care_negative.fullmatch(clean):
            social.update(care_enabled=False)
            for field in ('care_due', 'care_milestone', 'care_attention'):
                social.pop(field, None)
            self._save()
            return 'care_off'
        matched = bool(quoted and str(quoted) == row.get('receipt'))
        if row.get('pending') and matched and self.care_space.fullmatch(clean):
            row.update(pending=False, until=self.owner.clock() + 14*DAY)
            row.pop('receipt', None)
            social.pop('care_due', None)
            social.pop('care_milestone', None)
            self._save()
            return 'care_space'
        if row.get('pending') and (matched or self.care_positive.fullmatch(clean)):
            row.update(pending=False)
            row.pop('receipt', None)
            self._save()
        return False

    @staticmethod
    def maintain_care(social, now):
        row = social.get('care_attention', {})
        if not row:
            return False
        if row.get('pending') and now - row['sent'] >= CARE_WINDOW:
            row.update(pending=False, until=row['sent'] + 14*DAY)
            row.pop('receipt', None)
            return True
        if now >= row.get('until', 0):
            social.pop('care_attention', None)
            return True
        return False
