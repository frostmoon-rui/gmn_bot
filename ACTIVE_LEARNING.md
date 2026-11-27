# Active learning

The bot learns from explicit commands and feedback. It keeps these records locally and scopes them by signed-in bot account, speaker, and conversation. In groups, address the bot to use the commands below.

| Command | Effect |
| --- | --- |
| `remember moment: TEXT` | Save a brief memory in private chat for 30 days. Up to four recent, unexpired memories may be included when you ask the bot to recall a shared experience privately. |
| `forget moments` | Clear the saved moments and the reply-style preference in private chat. |
| `conversation feedback: brief` | Set a preference for this conversation and speaker. Other options are `formal`, `less slang`, and `casual`. |
| `follow up 2h TOPIC` | Schedule a private follow-up in 1–168 hours. Use `list follow-ups` or `cancel follow-ups` to manage it. |
| `check-ins on` / `check-ins off` | Enable or disable private cooling reminders. A reminder needs a recent comparable weather reading already in the bot cache. |
| `list features` | List registered tool IDs. The existing permission checks apply when tools are called. |
| `request feature: TEXT` | Save a private feature request for 30 days. `list feature requests` shows your current requests. |
| `show routines` | Show two-step proposals learned from recurring sequences of help, status, source lookup, and earthquake lookup. |
| `approve routine ID` / `revoke routine ID` | Control whether that proposal may be run. |
| `run routine ID` | Run an approved proposal through the existing tool dispatcher. |
| `schedule routine ID 2h` | Schedule one private run of an approved routine. Revoking approval cancels its pending run. |

The scheduler checks once a minute without fetching weather to evaluate cooling triggers. It honors notification preferences, a UTC+8 quiet period from 22:00 to 08:00, and a six-hour minimum interval per recipient. A follow-up can wait up to two days after its due time. State is saved before a proactive send so an uncertain acknowledgment will not cause a repeated send after restart.

Learning produces declarative proposals. Adding a new executable connector still requires implementation and review. Dialogue guidance carries the bot's established persona forward, treats nonverbal cues as ambiguous and distinguishes suggestions about costs or commitments from completed actions.

The bot excludes turns containing saved memories from rolling model history to preserve deletion and expiration. Records are maintained in the ignored `active_learning.local.json` file. Saved experiences can be included in model requests when you chat privately, so use only details you are comfortable sharing with the configured model provider.
