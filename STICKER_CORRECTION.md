# Reply-based image correction

Reply to an image sent by the bot, mention the bot explicitly and send exactly `delete`. Any member can use this correction: it is not an administrator command and does not block the person who reported the image.

The localized private runtime additionally accepts its localized delete command. Public source keeps English commands and responses; private records and language settings are not migrated.

## What happens

- The correction runs in a bounded background path, ahead of normal conversation/model work. No model or OCR request is needed.
- The bot verifies the quoted message's sender, conversation and returned message ID through OneBot `get_msg`. A matching successful-send receipt can recover image identity when that lookup or its URL has expired. Embedded user-provided quote metadata is not treated as proof.
- Known source-image digests are persisted first. The bot then requests `delete_msg` for that quoted message and obtains additional image bytes when available. Recall and download failures are reported distinctly; successful recall is not misrepresented as a successful image block.
- Exact byte digests, decoded pixel hashes and conservative thumbnail comparisons prevent future collection and sending of identical or nearly identical copies. Different text and different GIF frames remain distinct. Cropped, heavily edited or substantially re-encoded pictures may not match automatically.
- The live library entry is marked blocked, all its known aliases are suppressed, and random selection excludes it. The original file remains locally recoverable; unrelated images and personal records are untouched.
- A final outgoing-image guard covers an image already selected before correction. Already in-flight messages cannot be guaranteed to disappear automatically; quote a specific message to request its recall.

## Local data and lifecycle

The additive SQLite upgrade retains existing sticker rows, paths, captions and GIF bytes. The image blocklist and at most 10,000 compact successful-send records live in the existing private `stickers/index.sqlite3`; Base64 data is not duplicated in the message cache. Send records are scoped to the bot account and conversation, and are created only from successful OneBot receipts. Text-only messages, messages sent by another person and cross-conversation references are not recalled by this command.

Restart the bot to load the updated code. A running Python process does not reload edited modules automatically. QQ/NapCat recall permissions and time limits still apply: a recall can fail while the image is successfully blocked. If neither bytes nor a trusted send fingerprint can be recovered, the response says the image blocklist could not be completed.

Original retained files are a local recovery copy, not an active sending source. No real picture, fingerprint database, QQ identifier, credential or live message is included in the public repository. Tests use temporary generated PNG/GIF fixtures and fake WebSocket receipts only.
