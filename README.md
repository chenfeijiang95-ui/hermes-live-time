# live-time

Inject the **live current time** into every LLM call so a Hermes agent always
knows "now" — even in conversations that span multiple days or weeks.

## Why

Hermes stamps `Conversation started: ...` once at session creation and never
refreshes it. In long-lived or cross-day conversations the agent has no
reliable sense of the current date, weekday, or time of day unless it burns a
tool call to find out. This plugin closes that gap
([#10421](https://github.com/NousResearch/hermes-agent/issues/10421)).

## What it does

On every LLM call the plugin injects a small context block on the
user-message side of the request:

```
[LIVE-TIME] Now: 2026-08-13 18:20:00 (Weekday 4/7, 四), TZ Asia/Shanghai.
Injected by live-time plugin at THIS LLM call's moment. Use THIS as the
authoritative current time for any today/now/elapsed/date judgment. ...
```

Design properties:

- **Ephemeral** — appended to the request via the `pre_llm_call` hook, never
  written into session history or the cached system prompt (prompt caching
  unaffected).
- **Timezone aware** — resolves the timezone in this order:
  1. `HERMES_TIMEZONE` environment variable
  2. `timezone` key in `<HERMES_HOME | ~/.hermes>/config.yaml`
  3. local system timezone (reported as `TZ UTC±H`)
- **Zero dependencies** — stdlib only; no internal Hermes imports, so the
  plugin survives upstream refactors.

## Install

```bash
hermes plugins install chenfeijiang95-ui/hermes-live-time
```

or manually:

```bash
git clone https://github.com/chenfeijiang95-ui/hermes-live-time \
  "$HERMES_HOME/plugins/live-time"   # or ~/.hermes/plugins/live-time
```

Then restart Hermes and ask the agent "what time is it right now?" — it
should answer accurately without calling any tool.

## Upstream PR

The same code is proposed for the built-in `plugins/` tree in
[NousResearch/hermes-agent#85158](https://github.com/NousResearch/hermes-agent/pull/85158).
If that PR merges, this repo remains as the installable community source.

## Tests

```bash
python -m pytest tests/ -v
```

5 tests: timestamp freshness, authoritative marker, hook registration,
`HERMES_TIMEZONE` env resolution, and config-file timezone resolution.

## License

MIT
