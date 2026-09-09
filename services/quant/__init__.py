"""The quantitative engine: market data, indicators, strategies, backtesting,
and portfolio mathematics.

Everything in this package is independent of HTTP, the database, MCP, and the
LLM layer (architecture.md §5). A strategy or a backtest must be runnable from
a plain Python REPL, from a unit test, and from the API without knowing who
called it.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
