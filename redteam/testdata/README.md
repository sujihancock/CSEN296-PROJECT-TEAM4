# Red team test data

Canned inputs for the safe, sandboxed red team tests. Each `rt-0X.json` holds one suite:

- Top-level fields: `id`, `title`, `owasp`, `maestro`, and `attacks`.
- Each attack has an `id`, a `title`, a `category` (`guardrail` or `llm_policy`), an optional `setup`, a list of `steps`, and an `expect` block (`status_in`, `max_paid`).

The step types are `refund`, `create_case`, `kill_switch` and `tool_call`. A `tool_call` step can use `{case_id}` to refer to the case created earlier in the attack.

See ../plan.md and app/redteam.py.
