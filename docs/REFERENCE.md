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

Streaming size failures use response.failed events so Codex recognizes them during compaction. Use /compact and retry. Text and images are preserved without silent truncation or image quality reduction. An individually oversized attachment, tool definition, or result must be reduced at its source.

## Automatic conversation continuation

The app always enables automatic reuse with --continuation auto. CLI users retain --continuation auto|disabled for troubleshooting; auto is the default. The 19,000,000-byte guard applies in either mode.

When both Codex `session-id` and `thread-id` headers are present, the adapter fingerprints the fully normalized history and forwarded settings. An exact match with a completed, tool-free prior response sends only new items with Perplexity's `previous_response_id`, provided the new suffix has no local tool calls or results. Current tools and settings are still sent. Compaction, changed instructions or settings, changed tools or models, and unmatched histories start a fresh chain. Missing headers, restart, expiry, or eviction use full replay. Explicit client-supplied `previous_response_id` and `store: false` are preserved even when automatic continuation is disabled.

A response dispatching local tools invalidates its checkpoint even when its status is `completed`. Tool-result requests and repeated tool rounds send full normalized history, preserving calls, results, images, and order. A successfully delivered answer with no outstanding local tools establishes a new checkpoint, allowing ordinary continuation to resume. No earlier checkpoint carries a tool loop. Tool-heavy conversations can therefore still reach the 19 MB guard and need `/compact`.

Checkpoints contain provider response references, process-keyed HMAC fingerprints, and timestamps, held only in memory. They retain no chat text, images, or tool arguments after requests finish. The cache holds at most 128 chats and 16 MiB of fingerprint state, with 60-minute idle expiry and least-recently-used eviction. Requests within a chat are serialized through response completion; separate chats can run concurrently. Failed, malformed, incomplete, or cancelled responses invalidate checkpoints and earn no saved-byte credit. Checkpoints and savings commit only after successful complete delivery. Top-level streaming provider errors include the failed response envelope Codex expects, preserving original error fields.

If a generated prior reference is specifically rejected as unavailable, incomplete, or unsupported, the adapter invalidates it and tries full replay once before forwarding stream events. The fallback must pass the same byte guard. Unrelated errors, ambiguous connection failures, and errors after stream output begins do not trigger replay. Existing bounded 429 retries remain in place.

## Adapter-wide status

`GET /v1/adapter/status` requires a configured bearer token even when ordinary API authentication is disabled. It exposes numeric active-request and byte statistics, threshold counts at 10/12/15 MB, size failures, continuation/replay/fallback counts, `tool_boundary_replays`, cache-entry count, and saved request bytes. Tool replays count transmitted full-history requests containing local tool items once per client request, excluding transport retries and preflight rejections. The macOS window shows continuation activity, tool replays, and savings. These counters reset on restart. Savings compare successfully completed and delivered optimized requests with equivalent full replay; fallback attempts do not count as savings. No session identifiers, fingerprints, response IDs, or content appear in status.

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
