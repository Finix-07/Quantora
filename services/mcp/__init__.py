"""MCP server exposing domain-level tools to the AI orchestrator.

Only domain actions are exposed (`get_market_data`, `run_backtest`,
`compare_strategies`, ...). There is deliberately no `run_sql` and no
`execute_arbitrary_python`: the AI must never need to know the database schema
(architecture.md §3.5).
"""
