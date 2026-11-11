<p align="center">
  <img src="icon.png" alt="Codex–Perplexity Adapter logo" width="180">
</p>

# Codex–Perplexity Adapter

Use models available through Perplexity's Agent API in the Codex CLI, Codex app, and compatible IDE extensions. The adapter runs locally and translates requests between Codex and Perplexity.

## Download and setup

Download the appropriate macOS app from the [latest release](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest):

| Mac | Download |
| --- | --- |
| Apple Silicon (M1 or newer) | [Apple Silicon ZIP](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest/download/Codex-Perplexity-Adapter-macOS-arm64.zip) |
| Intel | [Intel ZIP](https://github.com/FredHutch/codex-perplexity-adapter/releases/latest/download/Codex-Perplexity-Adapter-macOS-x86_64.zip) |

Requires macOS 13 or newer. Unzip the app, move **Codex Perplexity Adapter.app** to Applications, and open it. Enter your Perplexity API key and choose **Start Adapter**. Keep the app open while using Codex.

The current apps are Developer ID signed, notarized by Apple, and include stapled notarization tickets. Check the installed version in **About Codex–Perplexity Adapter**. Press **⌘M** to minimize or **⌘H** to hide the app; the adapter keeps running.

## Configure Codex

Use Codex CLI 0.156.0 or newer, or a corresponding current Codex app or IDE extension. Add these settings to `~/.codex/config.toml`, keeping unrelated existing settings:

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

Then start Codex. The adapter must remain running while Codex is in use.

## Supported models

Choose `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna` using the model setting above. The aliases `gpt-6.1` and `gpt-5.6` select `gpt-6.1-sol` and `gpt-5.6-sol`, respectively.

## Current version

An **experimental 0.1.15 test candidate** adds optional resizing when a request exceeds 19 MB. It is off by default and is **not qualified for form review**: testing found lost decimal punctuation and misread identifiers in small text. The stable download remains v0.1.14. See [candidate verification](VERIFICATION_0.1.15.md).

**Current version: v0.1.14.** Supports requests up to 19 MB and displays the limit in the app. Conversation reuse is automatic. If a chat reaches the request limit, use `/compact` and retry.

Perplexity's API reports a **20 MB downstream transport limit** on the route used by this adapter. The adapter's **19 MB limit** leaves roughly 1 MB of headroom below it. This applies to the complete request, including conversation history, tools, and images; model token limits also apply. An individually oversized attachment must be reduced. See the [current release notes](RELEASE_NOTES.md).

## Troubleshooting

- **Codex cannot connect:** Start the adapter and check that Codex uses the configuration above.
- **Authentication error:** Check your Perplexity API key, then stop and restart the adapter with the correct key.
- **Request or context limit:** Use `/compact` and retry. Reduce oversized attachments or tool results at their source. Image-heavy chats can reach the limit even with conversation reuse enabled.
- **Rate or quota limit:** Wait and retry, or check usage and billing in your Perplexity account.

## Data handling

Conversation content, images, tool definitions, and tool results are sent to Perplexity over HTTPS. The app keeps your Perplexity API key in memory and does not save it. The adapter listens only on your computer at 127.0.0.1.

Perplexity retains conversation state for continuation. Setting `store: false` or disabling adapter reuse does **not** disable provider persistence. Submit only data approved for processing by Perplexity under your organization's policies.

For CLI options, request handling, the status API, and detailed data handling, see the [technical reference](docs/REFERENCE.md).

## License and project status

Released under the [MIT License](LICENSE). This is an independent, unofficial project, unaffiliated with OpenAI or Perplexity.
