# Optional standalone autonomy integration

Install the pinned autonomy dependency alongside the base requirements.
Autonomy is available in every group by default when the dependency is installed.
No group registration or separate opt-in is required.

~~~powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-autonomy.txt
python -m unittest discover -s tests -p test_autonomy_bridge.py -q
$env:ENABLE_AUTONOMY = 'false'
python -m unittest discover -s tests -q
$env:ENABLE_AUTONOMY = 'true'
python -m unittest discover -s tests -p test_main_offline.py -q
~~~

Leave ENABLE_AUTONOMY unset for default operation or set it to false to disable
the engine. Restart with the configured supervisor after changing the setting.
Ordinary private chat, addressed group chat and eligible group interjections use
the engine automatically. Natural questions about recent earthquakes or bot
status can dispatch the corresponding native tools. The direct memory commands
remain compatible. The host starts expiry, review and outreach workers after the
runtime initializes. The owner can use `autonomy emergency stop` and
`autonomy emergency resume` to pause or resume it for the running process.

The requirement pins the independent release tag and can be installed or rolled
back separately. This avoids a second checkout, submodule state and local import
path changes. Set the flag false to use only the original host behavior. The local SQLite file is ignored. The original social learning and
attention policies continue operating through their existing host entry points.
Unmatched events and engine failures fall through. Delivery and dispatch happen
after the decision and are sent only once through the existing host APIs.

The standalone tests use temporary SQLite files and a fake model. The host
tests cover the switch and missing dependency, the enabled bridge, an actual
message-handler dispatch, circuit breaking and heartbeat failure recovery.
None of the test commands starts the live bot.

## Conversational engine v0.3

Ordinary model-backed replies and the autonomy chat command gain a conversational
style policy. The adapter precomputes up to four natural bubbles and typing
intervals, then sends through the existing OneBot sender. Rich output and
explicit technical requests keep the existing rendering path. Any partial
transport failure is handled by the existing receipt path without replay.

Group candidates are still screened by the existing anti-interruption rules.
The engine also checks a rolling two-minute room window, local time and
persisted daily budgets. The four default UTC+8 phases are 07:00–17:00 normal,
17:00–22:00 active, 22:00–00:00 sleepy and 00:00–07:00 do not disturb.
Explicit commands and direct requests keep their original priority.

The worker reviews at least two delivered turns after a two-minute pause using an
available Gemini Flash model. It keeps no review transcripts on disk. User feedback
updates room and speaker preferences. Core facts have no expiry and eligible
episodes last 7 to 28 days. Scoped learning is on by default. Saying `don't
remember my messages` stops learning and clears learned state for that scope.
Public context contains anonymous topic identifiers. Private speech, group-only
notes and review evidence stay in their respective scopes.
Explicit autonomy memories remain a separate store and can be removed with
autonomy forget. The schema upgrade preserves older explicit records and
the optional flag can still be set to false for the original host path.

Private outreach requires a natural invitation such as `you can message me
first`. The worker reviews candidates, honors the host's notification and room
settings, and sends at most twice per account per day in UTC+8 daytime hours.
It waits for a private recipient to reply before trying again. `Don't message me
first` stops private outreach.

### QQ acceptance checks

- Send a short casual question in a group followed by a structured request
  for numbered instructions. Casual output uses natural bubbles and the
  requested structured answer keeps its formatting.
- In private, state a strong preference in ordinary language. Pause for two
  minutes and ask a related question. Try the same question in a group to check
  that private notes stay private.
- After mentioning an upcoming exam, invite the bot to check in the next day.
  Check that it respects its daytime and cooldown rules and waits for an answer.
- Compare a normal group chat with a dispute or urgent request and confirm the
  bot leaves room for the people speaking. Explicitly addressed requests still work.
