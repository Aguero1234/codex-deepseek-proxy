# codex-deepseek-proxy

> A lightweight proxy that bridges OpenAI Codex CLI with the DeepSeek API.
> Translates Codex's `/v1/responses` format to DeepSeek's `/v1/chat/completions` — with SSE streaming support.

## The Problem

Codex CLI expects the OpenAI Responses API (`/v1/responses`), but DeepSeek uses the Chat Completions API (`/v1/chat/completions`). They're not compatible.

## The Solution

```
Codex CLI → localhost:18888 → [proxy translates format] → DeepSeek API
```

This proxy:
1. Receives Codex's Responses API requests
2. Converts them to Chat Completions format
3. Forwards to DeepSeek
4. Converts the response back (including SSE streaming)
5. Returns to Codex

## Quick Start

```bash
# Set your DeepSeek API key
export DEEPSEEK_API_KEY="sk-your-key-here"

# Run the proxy
python proxy.py

# In another terminal, configure Codex CLI
codex --api-base http://127.0.0.1:18888/v1
```

That's it. Codex will now use DeepSeek as its backend.

## Features

- **Full SSE streaming** — real-time token-by-token output
- **Tool call support** — translates function calling between formats
- **Zero dependencies** — pure Python stdlib, no pip install needed
- **Lightweight** — single file, ~300 lines

## Configuration

| Env Variable | Default | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | Required | Your DeepSeek API key |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com/v1` | DeepSeek API base URL |
| `PROXY_PORT` | `18888` | Local proxy port |
| `DEBUG` | `false` | Enable request logging |

## How It Works

```
Codex sends:
  POST /v1/responses { model, input, instructions, stream, tools }

Proxy converts to:
  POST /v1/chat/completions { model, messages, stream, tools }

DeepSeek responds:
  SSE stream with deltas

Proxy converts back to:
  SSE events: response.output_text.delta → response.completed
```

## Use with Codex CLI config

Add to `~/.codex/config.toml`:

```toml
model = "deepseek-chat"
api_base_url = "http://127.0.0.1:18888/v1"
```

## License

MIT
