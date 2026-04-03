---
sidebar_position: 6
title: "Event Hooks"
description: "Run custom code at key lifecycle points — log activity, send alerts, post to webhooks"
---

# Event Hooks

Hermes has two hook systems that run custom code at key lifecycle points:

| System | Registered via | Runs in | Use case |
|--------|---------------|---------|----------|
| **[Gateway hooks](#gateway-event-hooks)** | `HOOK.yaml` + `handler.py` in `~/.hermes/hooks/` | Gateway only | Logging, alerts, webhooks |
| **[Plugin hooks](#plugin-hooks)** | `ctx.register_hook()` in a [plugin](/docs/user-guide/features/plugins) | CLI + Gateway | Tool interception, metrics, guardrails |

Both systems are non-blocking — errors in any hook are caught and logged, never crashing the agent.

## Gateway Event Hooks

Gateway hooks fire automatically during gateway operation (Telegram, Discord, Slack, WhatsApp) without blocking the main agent pipeline.

### Creating a Hook

Each hook is a directory under `~/.hermes/hooks/` containing two files:

```text
~/.hermes/hooks/
└── my-hook/
    ├── HOOK.yaml      # Declares which events to listen for
    └── handler.py     # Python handler function
```

#### HOOK.yaml

```yaml
name: my-hook
description: Log all agent activity to a file
events:
  - agent:start
  - agent:end
  - agent:step
```

The `events` list determines which events trigger your handler. You can subscribe to any combination of events, including wildcards like `command:*`.

#### handler.py

```python
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path.home() / ".hermes" / "hooks" / "my-hook" / "activity.log"

async def handle(event_type: str, context: dict):
    """Called for each subscribed event. Must be named 'handle'."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event": event_type,
        **context,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

**Handler rules:**
- Must be named `handle`
- Receives `event_type` (string) and `context` (dict)
- Can be `async def` or regular `def` — both work
- Errors are caught and logged, never crashing the agent

### Available Events

| Event | When it fires | Context keys |
|-------|---------------|--------------|
| `gateway:startup` | Gateway process starts | `platforms` (list of active platform names) |
| `session:start` | New messaging session created | `platform`, `user_id`, `session_id`, `session_key` |
| `session:end` | Session ended (before reset) | `platform`, `user_id`, `session_key` |
| `session:reset` | User ran `/new` or `/reset` | `platform`, `user_id`, `session_key` |
| `agent:start` | Agent begins processing a message | `platform`, `user_id`, `session_id`, `message` |
| `agent:step` | Each iteration of the tool-calling loop | `platform`, `user_id`, `session_id`, `iteration`, `tool_names` |
| `agent:end` | Agent finishes processing | `platform`, `user_id`, `session_id`, `message`, `response` |
| `command:*` | Any slash command executed | `platform`, `user_id`, `command`, `args` |

#### Wildcard Matching

Handlers registered for `command:*` fire for any `command:` event (`command:model`, `command:reset`, etc.). Monitor all slash commands with a single subscription.

### Examples

#### Boot Checklist (BOOT.md) — Built-in

The gateway ships with a built-in `boot-md` hook that looks for `~/.hermes/BOOT.md` on every startup. If the file exists, the agent runs its instructions in a background session. No installation needed — just create the file.

**Create `~/.hermes/BOOT.md`:**

```markdown
# Startup Checklist

1. Check if any cron jobs failed overnight — run `hermes cron list`
2. Send a message to Discord #general saying "Gateway restarted, all systems go"
3. Check if /opt/app/deploy.log has any errors from the last 24 hours
```

The agent runs these instructions in a background thread so it doesn't block gateway startup. If nothing needs attention, the agent replies with `[SILENT]` and no message is delivered.

:::tip
No BOOT.md? The hook silently skips — zero overhead. Create the file whenever you need startup automation, delete it when you don't.
:::

#### Telegram Alert on Long Tasks

Send yourself a message when the agent takes more than 10 steps:

```yaml
# ~/.hermes/hooks/long-task-alert/HOOK.yaml
name: long-task-alert
description: Alert when agent is taking many steps
events:
  - agent:step
```

```python
# ~/.hermes/hooks/long-task-alert/handler.py
import os
import httpx

THRESHOLD = 10
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_HOME_CHANNEL")

async def handle(event_type: str, context: dict):
    iteration = context.get("iteration", 0)
    if iteration == THRESHOLD and BOT_TOKEN and CHAT_ID:
        tools = ", ".join(context.get("tool_names", []))
        text = f"⚠️ Agent has been running for {iteration} steps. Last tools: {tools}"
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text},
            )
```

#### Command Usage Logger

Track which slash commands are used:

```yaml
# ~/.hermes/hooks/command-logger/HOOK.yaml
name: command-logger
description: Log slash command usage
events:
  - command:*
```

```python
# ~/.hermes/hooks/command-logger/handler.py
import json
from datetime import datetime
from pathlib import Path

LOG = Path.home() / ".hermes" / "logs" / "command_usage.jsonl"

def handle(event_type: str, context: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "command": context.get("command"),
        "args": context.get("args"),
        "platform": context.get("platform"),
        "user": context.get("user_id"),
    }
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

#### Session Start Webhook

POST to an external service on new sessions:

```yaml
# ~/.hermes/hooks/session-webhook/HOOK.yaml
name: session-webhook
description: Notify external service on new sessions
events:
  - session:start
  - session:reset
```

```python
# ~/.hermes/hooks/session-webhook/handler.py
import httpx

WEBHOOK_URL = "https://your-service.example.com/hermes-events"

async def handle(event_type: str, context: dict):
    async with httpx.AsyncClient() as client:
        await client.post(WEBHOOK_URL, json={
            "event": event_type,
            **context,
        }, timeout=5)
```

### How It Works

1. On gateway startup, `HookRegistry.discover_and_load()` scans `~/.hermes/hooks/`
2. Each subdirectory with `HOOK.yaml` + `handler.py` is loaded dynamically
3. Handlers are registered for their declared events
4. At each lifecycle point, `hooks.emit()` fires all matching handlers
5. Errors in any handler are caught and logged — a broken hook never crashes the agent

:::info
Gateway hooks only fire in the **gateway** (Telegram, Discord, Slack, WhatsApp). The CLI does not load gateway hooks. For hooks that work everywhere, use [plugin hooks](#plugin-hooks).
:::

## Plugin Hooks

[Plugins](/docs/user-guide/features/plugins) can register hooks that fire in **both CLI and gateway** sessions. These are registered programmatically via `ctx.register_hook()` in your plugin's `register()` function.

```python
def register(ctx):
    ctx.register_hook("pre_tool_call", my_callback)
    ctx.register_hook("post_tool_call", my_callback)
```

### Available Plugin Hooks

| Hook | Fires when | Callback receives |
|------|-----------|-------------------|
| `pre_tool_call` | Before any tool executes | `tool_name`, `args`, `task_id` |
| `post_tool_call` | After any tool returns | `tool_name`, `args`, `result`, `task_id` |
| `pre_llm_call` | Before LLM API request | `session_id`, `user_message`, `conversation_history`, `is_first_turn`, `model`, `platform` |
| `post_llm_call` | After LLM API response | `session_id`, `user_message`, `assistant_response`, `conversation_history`, `model`, `platform` |
| `on_session_start` | Session begins | `session_id`, `model`, `platform` |
| `on_session_end` | Session ends | `session_id`, `completed`, `interrupted`, `model`, `platform` |
| `on_model_change` | Model or provider changes | `old_model`, `new_model`, `old_provider`, `new_provider` |
| `post_update` | After `hermes update` completes | `update_status`, `prev_version`, `new_version`, `commits_count`, `hermes_home`, `project_root` |

#### Post-Update Hook

The `post_update` hook fires after `hermes update` completes, regardless of whether the update succeeded, failed, or made no changes.

```python
def register(ctx):
    ctx.register_hook("post_update", on_update)

def on_update(update_status, prev_version, new_version, commits_count, hermes_home, project_root):
    if update_status == "success":
        print(f"Updated {prev_version[:8]} → {new_version[:8]} ({commits_count} commits)")
```

**Parameters:**
- `update_status` — `"success"`, `"failed"`, or `"no_changes"`
- `prev_version` — Git SHA before update (or empty string)
- `new_version` — Git SHA after update (or empty string)
- `commits_count` — Number of commits pulled
- `hermes_home` — Path to Hermes home directory
- `project_root` — Path to Hermes installation

:::tip
The `post_update` hook also runs **executable scripts** from `~/.hermes/post-update.d/` — see [Post-Update Scripts](#post-update-scripts) below.
:::

#### Model Change Hook

The `on_model_change` hook fires whenever the active model or provider changes (via `hermes model`, `hermes use`, or gateway `/model` command).

```python
def register(ctx):
    ctx.register_hook("on_model_change", on_model_change)

def on_model_change(old_model, new_model, old_provider, new_provider):
    """React to model/provider switches."""
    print(f"🔄 Model changed: {old_provider}/{old_model} → {new_provider}/{new_model}")
    
    # Example: Notify companion agents via shared memory
    if old_provider != new_provider:
        print(f"   Provider switched from {old_provider} to {new_provider}")
    
    # Example: Log to external system
    # requests.post("https://api.example.com/log", json={...})
```

**Parameters:**
- `old_model` — Previous model identifier (e.g., "gpt-4o")
- `new_model` — New model identifier (e.g., "claude-sonnet-4")
- `old_provider` — Previous provider (e.g., "openai")
- `new_provider` — New provider (e.g., "anthropic")

Callbacks receive keyword arguments matching the columns above:

```python
def my_callback(**kwargs):
    tool = kwargs["tool_name"]
    args = kwargs["args"]
    # ...
```

### Example: Block Dangerous Tools

```python
# ~/.hermes/plugins/tool-guard/__init__.py
BLOCKED = {"terminal", "write_file"}

def guard(**kwargs):
    if kwargs["tool_name"] in BLOCKED:
        print(f"⚠ Blocked tool call: {kwargs['tool_name']}")

def register(ctx):
    ctx.register_hook("pre_tool_call", guard)
```

See the **[Plugins guide](/docs/user-guide/features/plugins)** for full details on creating plugins.

---

## Post-Update Scripts

In addition to plugin hooks, Hermes supports running **executable scripts** from `~/.hermes/post-update.d/` after `hermes update` completes. This is useful for automation that doesn't require a full Python plugin.

### How It Works

1. Create the directory: `mkdir -p ~/.hermes/post-update.d`
2. Drop executable scripts into it (bash, Python, Node.js, or any compiled binary)
3. Scripts run automatically after each update, sorted alphabetically by filename
4. Each script receives update context via environment variables

### Supported Script Types

Scripts must have the **executable bit set** (`chmod +x`) to run. The following extensions are recognized and run with the appropriate interpreter:

| Type | Extension | Interpreter | Requirements |
|------|-----------|-------------|--------------|
| Shell | `.sh` | `bash` | Bash installed |
| Python | `.py` | `python3` | Python 3 installed |
| Node.js | `.js` | `node` | Node.js installed |
| Perl | `.pl` | `perl` | Perl installed |
| Ruby | `.rb` | `ruby` | Ruby installed |
| Binary | any | direct | Executable bit set, no extension needed |

### Example: Notification Script

Create the script with the shebang on the first line:

```bash
#!/bin/bash
# ~/.hermes/post-update.d/01-notify.sh

if [ "$HERMES_UPDATE_STATUS" = "success" ]; then
    echo "✅ Hermes updated: ${HERMES_PREV_VERSION:0:8} → ${HERMES_NEW_VERSION:0:8}"
    echo "   Pulled $HERMES_COMMITS_COUNT commit(s)"
fi
```

Make it executable:
```bash
chmod +x ~/.hermes/post-update.d/01-notify.sh
```

### Example: Node.js Script

```javascript
#!/usr/bin/env node
// ~/.hermes/post-update.d/02-log.js

console.log(`Update status: ${process.env.HERMES_UPDATE_STATUS}`);
console.log(`Commits: ${process.env.HERMES_COMMITS_COUNT}`);
```

Make it executable:
```bash
chmod +x ~/.hermes/post-update.d/02-log.js
```

### Environment Variables

Scripts receive these environment variables:

| Variable | Value |
|----------|-------|
| `HERMES_UPDATE_STATUS` | `"success"` \| `"failed"` \| `"no_changes"` |
| `HERMES_PREV_VERSION` | Git SHA before update (or empty) |
| `HERMES_NEW_VERSION` | Git SHA after update (or empty) |
| `HERMES_COMMITS_COUNT` | Number of commits pulled |
| `HERMES_HOME` | Path to Hermes home directory (default: `~/.hermes`) |
| `HERMES_SCRIPTS_DIR` | Path to the `post-update.d` scripts directory |

### Ordering

Scripts run in alphabetical order by filename. Use numeric prefixes to control order:

```
~/.hermes/post-update.d/
├── 01-check-dependencies.sh
├── 02-sync-config.py
├── 03-restart-gateway.sh
└── 99-notify-discord.js
```

### Error Handling

- **Non-fatal**: Script failures don't block the update or other scripts
- **Timeout**: Each script gets 5 minutes, then is killed
- **Output**: Scripts inherit the parent output streams (stdout/stderr visible in terminal)
- **Logging**: Exit codes are logged for debugging

### Skipping Scripts

Use the `--no-hooks` flag to skip all post-update automation (both plugin hooks and scripts):

```bash
hermes update --no-hooks
```

### Comparison: Scripts vs. Plugins

| Feature | Scripts | Plugins |
|---------|---------|---------|
| Installation | Drop files in `post-update.d/` | Install via `hermes plugins install` |
| Language | Any executable | Python only |
| Access to Hermes internals | ❌ No | ✅ Yes |
| Can modify agent behavior | ❌ No | ✅ Yes |
| Best for | Simple automation | Complex logic, API calls, state management |
