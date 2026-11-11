# Technical reference

Advanced configuration and implementation details. See the [user guide](../README.md) for setup.

## Request compatibility

Requests and responses are forwarded by default. The adapter replaces the public model alias with its upstream route, removes Codex-only `client_metadata` that Perplexity rejects, and converts Codex custom tools when needed. Namespaced tools receive unique upstream names, and their namespace is restored on tool calls returned to Codex so it can dispatch them to the right plugin. Desktop tool-result notifications without a call ID are preserved as user-role notification text; paired results retain their call IDs and omit unsupported client namespace/name fields. Standard `metadata` is forwarded. For `openai/gpt-6-astra` and `openai/gpt-6.1-sol`, it changes `none` or `minimal` reasoning effort to `low` and removes `temperature` and `top_p`. Compatibility follows the resolved upstream route, including custom routes, pinned routes, and passthrough. Other routes receive no model-specific normalization. Perplexity validates forwarded fields and model capabilities; its errors are returned to the client.

Image content in tool results is preserved. Codex image detail `original` is mapped to `high`.

When Codex replays a conversation containing several explicit developer mode instructions, the adapter retains only the latest `<collaboration_mode>` block. This prevents obsolete Plan instructions from competing with a later Default instruction. Other developer instructions, including permissions, stay in place. User messages, assistant replies, and tool results cannot change the mode through this conversion.

## CLI model routing

Add or replace routes with repeatable `--model-route ALIAS=UPSTREAM` options. The last occurrence of an alias wins, including over a built-in route:

```text
codex-perplexity-adapter \
  --model-route team-model=preset/team-coding \
  --model-route gpt-6-sol=openai/gpt-6-sol
```

`--allow-model-passthrough` forwards unknown, syntactically valid `gpt-*` names as `openai/<name>`. Such names are not shown by `/v1/models`. This option may expose new models with unverified request or tool compatibility, and Perplexity may reject models unavailable to your account. Strict routing is the default.

The legacy options retain single-route behavior. Supplying either option exposes only that route, using `gpt-5.6-sol` or `openai/gpt-5.6-sol` as the default for an omitted side. They cannot be combined with `--model-route`:

```text
codex-perplexity-adapter \
  --model-alias team-model \
  --upstream-model preset/team-coding
```

## Rate-limit retries

On an upstream HTTP 429, the adapter makes up to two additional attempts before returning the error to Codex. It waits 1 then 2 seconds by default, or follows Perplexity's Retry-After header when the requested delay is at most 15 seconds. Longer requested waits return the 429 immediately, including Retry-After, so Codex can handle it. Persistent quota limits still require action on the Perplexity account.

## Request size limits

Perplexity's downstream transport limit applies to the entire request, including instructions, tool definitions, conversation text, tool results, and attachments. The adapter checks the actual UTF-8 JSON body against a 19,000,000-byte budget, leaving roughly 1 MB below the 20 MB downstream limit observed on this route. Oversized requests return `context_length_exceeded` so Codex recognizes the context limit. The same conversion applies when Perplexity reports its downstream limit in an HTTP error or failed streaming response. Other provider errors retain their original behavior.

Streaming size failures use response.failed events so Codex recognizes them during compaction. Use /compact and retry. With resizing off, text and images are preserved without silent truncation or image quality reduction. An individually oversized attachment, tool definition, or result must be reduced at its source.

## Oversized-image recovery

The CLI option --resize-images-on-limit enables recovery, where it is off by default. The macOS app provides the saved “Resize images in oversized requests” checkbox, initially enabled. Changes apply after Stop/Start. The adapter uses port **4000**.

After normalization and continuation selection, bodies at or below 19,000,000 bytes pass unchanged. Larger bodies try inline JPEG/PNG user and tool images in oldest-first occurrence order. Each long edge steps down by 15% to a 1,024-pixel floor, aiming for 18,000,000 bytes. Candidates come from the original pixels with Lanczos; JPEG keeps subsampling at quality 85, PNG keeps its format and transparency. Only smaller encoded candidates are retained. Newer images remain original if earlier changes suffice. If the target cannot be reached, a body within the hard guard is accepted; otherwise the normal compaction error remains. No image or text is removed.

Orientation is applied to pixels; stale orientation/dimension/thumbnail tags are removed. ICC and supported metadata are retained. Remote URLs, other formats, assistant images, embedded text/arguments, malformed/truncated images, animation, CMYK, HDR/MPF and unsupported metadata pass unchanged. Bounds are 32 MiB of base64 per image, 20 million pixels per image, 500 million decoded pixels per client request, and a 32 MiB request-local candidate memo. Dimensions are reserved before loading pixels; every decode attempt counts, including failed decoding and decoding again after memo eviction. A memo hit does not count again. Processing stops before an eligible decode would exceed the remaining allowance. No decoded-image cache is retained.

Preparation allows 60 seconds of active preparation, shared between the selected request and any structured-reference fallback. Queue and upstream network time do not count. This is a soft deadline checked before decoding and between resize/encode operations: an operation already running may finish. Each candidate comes lazily from original pixels; processing and generators stop as soon as the target is reached. At exhaustion the best completed result is serialized and sent if within 19 MB; otherwise the context-limit error includes the exhausted budget. Transport retries reuse prepared bytes. One worker runs per server; cancellation signals the worker and retains its slot until native processing ends. Cancelled requests transmit nothing and earn no savings. Operational logs contain elapsed time, decoded pixels, processed/skipped counts, original/final byte counts and stop reason, without image or conversation content. No image cache survives a request.

Checkpoints bind original normalized client history and the resize policy to a completed provider response. Selected requests and structured prior-reference fallback are prepared independently from original normalized history; transport retries reuse identical bytes. Existing continuation and fallback eligibility is unchanged. Provider transport, model and visual-context limits still apply.

Authenticated numeric counters `image_resize_requests`, `resized_images` (occurrences), and `image_saved_request_bytes` count only the final completed and delivered attempt. “Image saved” is separate from “Saved”, which remains continuation savings before resizing. Failed responses, cancelled delivery, preflight rejection, omitted history and transport retries earn no image credit. Counters reset on restart.

**Small-text readability is not qualified.** Synthetic form OCR lost decimal punctuation and misread identifiers at smaller resolutions. Turn resizing off for precision form review. The user-approved recovery behavior is released with this limitation. See [release verification](../VERIFICATION_0.1.15_RELEASE.md).

## Automatic conversation continuation

The app always enables automatic reuse with --continuation auto. CLI users retain --continuation auto|disabled for troubleshooting; auto is the default. The 19,000,000-byte guard applies in either mode.

When both Codex `session-id` and `thread-id` headers are present, the adapter fingerprints the fully normalized history and forwarded settings. An exact match with a completed, tool-free prior response sends only new items with Perplexity's `previous_response_id`, including when the suffix contains local tool calls and results. Current tools and settings are still sent. Compaction, changed instructions or settings, changed tools or models, and unmatched histories start a fresh chain. Missing headers, restart, expiry, or eviction use full replay. Explicit client-supplied `previous_response_id` and `store: false` are preserved even when automatic continuation is disabled.

A successful response dispatching local tools preserves only the previously matched completed-answer checkpoint. It cannot establish a new checkpoint. Tool-result requests send that answer’s reference plus every item after its saved prefix, preserving calls, results, images, reasoning and order. A successfully delivered final answer without outstanding local calls advances the checkpoint. Before such an answer exists, after compaction, or when matching fails, the adapter sends full history. Images added after the anchor accumulate in the suffix until the final answer, so either the suffix or a full replay can still reach the 19 MB guard. The adapter never continues directly from a tool-bearing response. Existing live evidence qualifies earlier-answer reuse on the tested Perplexity route and gpt-6.1-sol; other model/provider behavior can differ.

Checkpoints contain provider response references, process-keyed HMAC fingerprints, and timestamps, held only in memory. They retain no chat text, images, or tool arguments after requests finish. The cache holds at most 128 chats and 16 MiB of fingerprint state, with 60-minute idle expiry and least-recently-used eviction. Requests within a chat are serialized through response completion; separate chats can run concurrently. Failed, malformed, incomplete, or cancelled responses invalidate checkpoints and earn no saved-byte credit. Checkpoints and savings commit only after successful complete delivery. Top-level streaming provider errors include the failed response envelope Codex expects, preserving original error fields.

If a generated prior reference is specifically rejected as unavailable, incomplete, or unsupported, the adapter invalidates it and tries full replay once before forwarding stream events. The fallback must pass the same byte guard. Unrelated errors, ambiguous connection failures, and errors after stream output begins do not trigger replay. Existing bounded 429 retries remain in place.

## Adapter-wide status

`GET /v1/adapter/status` requires a configured bearer token even when ordinary API authentication is disabled. It exposes numeric active-request and byte statistics, threshold counts at 10/12/15 MB, size failures, continuation/replay/fallback counts, `tool_boundary_replays`, cache-entry count, and saved request bytes. Tool replays count transmitted full-history requests containing local tool items once per client request, excluding transport retries and preflight rejections. The macOS window shows continuation activity, tool replays, and savings. These counters reset on restart. Continuation savings compare the selected body before resizing with equivalent original full replay; fallback attempts do not count as savings. No session identifiers, fingerprints, response IDs, or content appear in status.

Active request and response buffers remain in memory until completion, cancellation, or failure; secure erasure and protection from OS swap or crash capture are not guaranteed. HTTP/SSE transport is unchanged. There is no WebSocket transport, retrieval endpoint, disk cache, or image-hosting feature.

## Data handling and governance

- **Data forwarded:** The adapter forwards Responses request fields by default. These can include prompts, conversation context, metadata or user identifiers, multimodal content or URLs, tool definitions, tool arguments, and tool results. It does not redact this data. Documented compatibility conversions, model routing, and exact conversation continuation are the intended changes.
- **External destination:** Requests go to the hard-coded Perplexity Agent API endpoint over HTTPS. Custom routes and model passthrough change the model requested from Perplexity, not the network destination. The adapter contains no analytics or telemetry integrations.
- **Tool execution:** Codex executes client-side tools, including browser and computer tools. The adapter forwards their definitions, calls, and returned results as conversation data; it does not execute them. Provider-hosted tools, where supported, are handled by Perplexity.
- **Credentials and local access:** The macOS app keeps the entered Perplexity API key in memory and passes it to its local server process through an environment variable. The server sends it to Perplexity in the authorization header; the app does not save it. The documented local bearer token is a fixed, public default, so other software on the same machine that knows it can call the adapter. CLI users can set a unique token with `ADAPTER_LOCAL_TOKEN` or `--local-token` and use the same value in Codex configuration.
- **Provider persistence:** [Perplexity documents](https://docs.perplexity.ai/docs/agent-api/conversation-state) that conversation state persists server-side for continuation. `store: false` hides retrieval but does not disable persistence. The CLI's `--continuation disabled` controls adapter reuse; it is not a provider retention opt-out.
- **Storage and logs:** Memory-only checkpoints are described above. The adapter has no database and does not intentionally log or persist request bodies, response bodies, or tool arguments. Access logging is disabled. The macOS app replaces its operational error log at `~/Library/Logs/Codex Perplexity Adapter.log` each time it starts.
- **Network boundary:** The service listens only on `127.0.0.1` and has no command-line host option.
- **Organizational responsibility:** Submit only data approved for processing by Perplexity under your organization's policies, agreements, and retention controls. Review Perplexity's terms for any downstream model-provider processing. The adapter cannot change Perplexity's retention or processing practices.
