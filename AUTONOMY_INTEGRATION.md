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
Address the bot in a group with autonomy to see the guide, autonomy chat: QUESTION
to get a group-safe model reply, or autonomy run status to invoke the native
dispatcher. Group dialogue uses only the current question and never reads private
memories. In private, autonomy remember: TEXT, autonomy recall and autonomy forget
expose the memory store directly. The host starts the expiry heartbeat when
the runtime initializes successfully.

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
