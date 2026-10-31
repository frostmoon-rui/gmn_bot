# Exact-file recognition reuse

Vision checks the SHA-256 of the original file before image preparation or model calls. Successful results are stored locally in `vision_cache.sqlite3`; memory eviction, a different download URL or a bot restart do not require another call for the same file and request. Concurrent duplicates share one task, and cancelling one caller does not cancel other callers.

Generic sticker captions and question-specific image observations remain separate. A new question can need details not present in a brief caption, so its successful result gets a separate request fingerprint. Raw questions and image bytes are not stored in this cache. Modified image bytes and GIFs with different animation data are not treated as the same file merely because a thumbnail looks similar.

Automatic sticker recognition first checks the existing sticker database for a caption associated with the exact file SHA. Blacklisted originals and aliases remain excluded. A duplicate with no successful caption can still be recognized. Empty/failed results are only briefly suppressed in memory, never saved permanently as successful observations. Cache I/O errors preserve existing files and do not invalidate a successful model result.

Runtime databases and the sticker library remain private local data and must not be committed to GitHub. Terminal logs identify file-cache hits and shared requests; no extra QQ notifications are sent.

After an exact-file miss, static PNG/JPEG/WebP files may reuse an observation only
when their fully decoded RGBA pixels and dimensions match exactly. This is not
perceptual matching. Animated formats, GIFs, EXIF-bearing images, colour-profiled
images and oversized inputs retain exact-file-only behavior. Different questions
remain separate, including across pixel aliases. Original image files and sticker
moderation rules are never rewritten by observation caching.

Concurrent equivalent static encodings share a cancellable-waiter-safe recognition
task. The memory tier keeps at most 256 entries and a bounded alias index.
Pixel decoding is serialized separately from model requests and hashes small row
bands, avoiding a burst of simultaneous full-frame allocations for large images.
Successful disk results survive restart; failed observations remain short-lived in memory.
Request keys normalize Unicode NFC and line endings without merging different
questions or discarding interior whitespace. Ordinary conversations are not shared
answer-cache entries. Public-page searches recheck their exact query/mode/topic
cache after waiting for the process gate, avoiding duplicate queued retrievals.

See [resource maintenance](RESOURCE_MAINTENANCE.md) for expiration and retention.
