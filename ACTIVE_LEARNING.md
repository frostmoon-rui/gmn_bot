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

## Interaction review

Group interaction learning is enabled by default. Private learning requires `interaction learning on`. The worker samples addressed
conversations with a delivered bot reply and submits only a short suitable message, relevant
prior bot reply for explicit feedback, and tool IDs to Gemini. The worker has at most one
request in flight, a 24-entry queue, a five-minute queue lifetime, and a global 30-second
minimum interval. Recent feedback guides the next reply. The relationship graph consists
of bot-to-speaker edges, scoped by the bot account and the speaker. Group feedback and explicitly chosen group reply styles are shared across groups. Private records have their own scope.
It does not infer relationships between chat members.

- `interaction profile` shows feedback counts. `learned moments` lists private, summarized
  experiences that can be recalled in the ordinary private chat flow. Group messages
  supply interaction feedback. Use `remember group moment: TEXT` while addressing the
  bot to save a memory that you can recall explicitly in any group with that bot.
  `forget group moments` removes those memories across groups. Private experiences
  are never included in group recall. Reviews, moments and drafts expire after 30 days.
- At existing passive reply opportunities, the conversation model may choose silence.
  An explicit request to give the chat space pauses passive interjections for 15 minutes.
- `care follow-ups on` enables private check-ins roughly one day after an explicit invitation or after a volunteered, near-term milestone such as an exam or presentation. `care follow-ups off` cancels the pending learned check-in. Quiet hours,
  notification preferences and existing recipient limits apply.
- `review workflows` lists Gemini's proposals. `accept reviewed workflow ID` can approve a
  validated pair of existing read-only blocks. Missing connectors remain review-only
  drafts. An approved workflow uses the existing manual or private scheduled runner.
- `interaction learning off` clears the learned records, in-flight results, and pending
  learned check-ins in private, or across all groups when used in a group. Explicitly configured reminders and workflows
  that the user already approved continue to use their existing controls.
