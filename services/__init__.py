"""AI Quant Terminal service packages.

`services.quant` is the framework-agnostic quantitative engine (data,
indicators, strategies, backtest, portfolio math). `services.mcp` is the MCP
server that exposes domain-level tools to the AI layer.

They ship as one deployable container (`quant-mcp`, architecture.md §19) but
stay logically separate: the MCP layer calls the quant engine as a library, in
process, never over the network. Note the package is `services.mcp`, not a
top-level `mcp`, so it cannot shadow the official MCP SDK package.
"""
