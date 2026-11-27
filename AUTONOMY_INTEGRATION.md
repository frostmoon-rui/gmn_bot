# Optional standalone autonomy integration

Keep the existing requirements and startup as the default. Install the pinned
dependency and opt in only on the integration branch:

~~~powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-autonomy.txt
python -m unittest discover -s tests -p test_autonomy_bridge.py -q
$env:ENABLE_AUTONOMY = 'false'
python -m unittest discover -s tests -q
$env:ENABLE_AUTONOMY = 'true'
python -m unittest discover -s tests -p test_main_offline.py -q
~~~

For a deployment, set ENABLE_AUTONOMY to true in the process environment or the
host's ignored local settings. Restart with its configured supervisor. Start
with a private-chat test of autonomy remember: TEXT, autonomy chat: TEXT, and
autonomy forget. An addressed autonomy run status uses the native tool
dispatcher, for both group and private messages. The host starts the expiry
heartbeat when the runtime initialized successfully.

The requirement pins the independent release tag and can be installed or rolled
back separately. This avoids a second checkout, submodule state and local import
path changes. Leave the flag false for a deployment that uses the original
host behavior. The local SQLite file is ignored. The original social learning and
attention policies continue operating through their existing host entry points.
Unmatched events and engine failures fall through. Delivery and dispatch happen
after the decision and are sent only once through the existing host APIs.

The standalone tests use temporary SQLite files and a fake model. The host
tests cover the switch and missing dependency, the enabled bridge, an actual
message-handler dispatch, circuit breaking and heartbeat failure recovery.
None of the test commands starts the live bot.
