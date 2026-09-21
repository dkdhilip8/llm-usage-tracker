"""Provider adapters: one module per provider, each owning its own request
construction and response parsing. See base.py for the shared contract
(UsageInfo) and app/gateway.py for the governance funnel every adapter sits
behind (auth, workspace/provider/model ACL, budgets, caps) — no adapter is
reached except through that funnel, and no adapter bypasses
app.providers.send_request/stream_request (the one outbound-HTTP chokepoint)."""
