# Chat delivery and interaction style

Passive interjections retain `[SPLIT]` bubble boundaries and ordinary complete replies beyond 30 characters. Their soft length budget is 120 characters: longer text prefers a complete sentence boundary, may finish a first sentence within twice that budget, and uses an explicit ellipsis for pathological unpunctuated output. Active replies do not use this passive shortener. Hidden model update tags and reasoning are removed without executing their actions.

Gemini `MAX_TOKENS` finishes are logged separately from local formatting, so generation limits can be distinguished from delivery trimming without adding speculative retries or extra model calls.

The bot can be playful, lightly flirtatious with receptive adults, and use occasional brief affectionate roleplay such as a hug or leaning on a shoulder. It follows the person's confirmed name and pronouns; transgender identity, HRT or feminine presentation do not establish age, gendered forms of address or consent. Unknown-age interactions remain nonsexual. It stops intimate teasing when declined, when someone is distressed or when the conversation becomes serious. Random interjections do not initiate intimate invitations toward uninvolved members. No private profiles or identities are rewritten for this style update.
