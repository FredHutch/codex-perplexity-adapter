<p align="center">
  <img src="icon.png" alt="Codex–Perplexity Adapter logo" width="180">
</p>

# Codex–Perplexity Adapter

A local compatibility service that enables the Codex CLI, Codex app, and compatible IDE extensions to use models available through Perplexity's Agent API. Because Codex and Perplexity use different request, response, streaming, and tool-call formats, an API key alone is insufficient; the adapter translates between them.

```text
Codex CLI, app, or IDE extension
        │
        ▼
Local adapter on 127.0.0.1:4000
        │  HTTPS
        ▼
Perplexity Agent API
```

## Download and setup

Download the appropriate macOS app from the [latest release](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest):

| Mac | Download |
| --- | --- |
| Apple Silicon (M1 or newer) | [Codex-Perplexity-Adapter-macOS-arm64.zip](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest/download/Codex-Perplexity-Adapter-macOS-arm64.zip) |
| Intel | [Codex-Perplexity-Adapter-macOS-x86_64.zip](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest/download/Codex-Perplexity-Adapter-macOS-x86_64.zip) |

The apps require macOS 13 or newer. After unzipping, move **Codex Perplexity Adapter.app** to Applications, open it, enter a Perplexity API key, and choose **Start Adapter**. Keep the app open while using Codex.

The v0.1.10 apps are signed with an Apple Developer ID, notarized by Apple, and include stapled notarization tickets.
Choose **About Codex–Perplexity Adapter** from the app menu to see the installed version and its supported model aliases.

Press **⌘M** to minimize the window or **⌘H** to hide the app. The adapter continues running; restore it from the Dock.

Use Codex CLI 0.156.0 or newer, or a corresponding current release of the Codex app or IDE extension, for the models listed below.

## Configure Codex

Modify `~/.codex/config.toml` to include these settings. Keep any unrelated existing settings:

```toml
model = "gpt-6.1-sol"
model_provider = "perplexity_adapter"
model_reasoning_effort = "medium"

[model_providers.perplexity_adapter]
name = "Perplexity Adapter"
base_url = "http://127.0.0.1:4000/v1"
wire_api = "responses"
experimental_bearer_token = "local-adapter-token"
stream_idle_timeout_ms = 300000
```

Then start Codex using the CLI (`codex`), the Codex app, or a compatible IDE extension. The adapter must remain running while Codex is in use.

## Current release ([v0.1.10](https://github.com/FredHutch/codex-perplexity-adapter/releases/tag/v0.1.10))

The adapter supports `gpt-6.1-sol`, `gpt-6.1`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and `gpt-5.6`. Set the `model` value above to the model you want to use. `gpt-6.1` is an alias for `gpt-6.1-sol`; `gpt-5.6` is an alias for `gpt-5.6-sol`.

Requests and responses are forwarded by default. The adapter replaces the public model alias with its upstream route, removes Codex-only `client_metadata` that Perplexity rejects, and converts Codex custom tools when needed. Namespaced tools receive unique upstream names, and their namespace is restored on tool calls returned to Codex so it can dispatch them to the right plugin. Standard `metadata` is forwarded. For `openai/gpt-6-astra` and `openai/gpt-6.1-sol`, it changes `none` or `minimal` reasoning effort to `low` and removes `temperature` and `top_p`. Compatibility follows the resolved upstream route, including custom routes, pinned routes, and passthrough. Other routes receive no model-specific normalization. Perplexity validates forwarded fields and model capabilities; its errors are returned to the client.

Version 0.1.10 preserves image content in tool results so Codex can inspect images and retain them in conversation history. Codex's image detail hint `original` is mapped to the supported `high` hint. Requests above the 18 MB adapter budget and Perplexity's downstream 20 MB transport failures return a context-limit error that Codex can recognize. Use `/compact` in affected chats and retry. Text and image content are preserved. See [v0.1.10 release notes](RELEASE_NOTES.md) for verification and limitations.

When Codex replays a conversation containing several explicit developer mode instructions, the adapter retains only the latest `<collaboration_mode>` block. This prevents obsolete Plan instructions from competing with a later Default instruction. Other developer instructions, including permissions, stay in place. User messages, assistant replies, and tool results cannot change the mode through this conversion.

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

On an upstream HTTP 429, the adapter makes up to two additional attempts before returning the error to Codex. It waits 1 then 2 seconds by default, or follows Perplexity's Retry-After header when the requested delay is at most 15 seconds. Longer requested waits return the 429 immediately, including Retry-After, so Codex can handle it. Persistent quota limits still require action on the Perplexity account.

### Request size limits

Perplexity's downstream transport limit applies to the entire request, including instructions, tool definitions, conversation text, tool results, and attachments. The adapter checks the actual UTF-8 JSON body against an 18,000,000-byte budget, leaving headroom below the downstream 20 MB limit. Oversized requests return `context_length_exceeded` so Codex recognizes the context limit. The same conversion applies when Perplexity reports its downstream limit in an HTTP error or failed streaming response. Other provider errors retain their original behavior.

If a conversation reaches this limit, use `/compact` and retry. The adapter preserves all text and image data; it does not silently truncate tool results, remove messages, or reduce image quality. A single attachment, tool definition, or result that exceeds the budget must be reduced at its source.

## Data handling and governance

- **Data forwarded:** The adapter forwards Responses request fields by default. These can include prompts, conversation context, metadata or user identifiers, multimodal content or URLs, tool definitions, tool arguments, and tool results. It does not redact this data. Documented compatibility conversions and model routing are the only intended changes.
- **External destination:** Requests go to the hard-coded Perplexity Agent API endpoint over HTTPS. Custom routes and model passthrough change the model requested from Perplexity, not the network destination. The adapter contains no analytics or telemetry integrations.
- **Tool execution:** Codex executes client-side tools, including browser and computer tools. The adapter forwards their definitions, calls, and returned results as conversation data; it does not execute them. Provider-hosted tools, where supported, are handled by Perplexity.
- **Credentials and local access:** The macOS app keeps the entered Perplexity API key in memory and passes it to its local server process through an environment variable. The server sends it to Perplexity in the authorization header; the app does not save it. The documented local bearer token is a fixed, public default, so other software on the same machine that knows it can call the adapter. CLI users can set a unique token with `ADAPTER_LOCAL_TOKEN` or `--local-token` and use the same value in Codex configuration.
- **Storage and logs:** The adapter has no database and does not intentionally log or persist request bodies, response bodies, or tool arguments. Access logging is disabled. The macOS app replaces its operational error log at `~/Library/Logs/Codex Perplexity Adapter.log` each time it starts.
- **Network boundary:** The service listens only on `127.0.0.1` and has no command-line host option.
- **Organizational responsibility:** Submit only data approved for processing by Perplexity under your organization's policies, agreements, and retention controls. Review Perplexity's terms for any downstream model-provider processing. The adapter cannot change Perplexity's retention or processing practices.

## License and project status

Released under the [MIT License](LICENSE).

This is an independent, unofficial project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Perplexity. Codex, OpenAI, Perplexity, and related marks belong to their respective owners.
