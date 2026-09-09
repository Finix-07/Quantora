# AI Quant Terminal — Product Manager Handoff

> **Audience:** Product intern / Product-minded engineering intern  
> **Purpose:** Define what should be built, why it matters, how a user should experience it, and what “done” means.

---

## 1. Product Vision

Build a **single-user AI Quant Terminal** that helps a trader research markets, test trading ideas, understand portfolio risk, and learn from past decisions through natural-language interaction.

The product should feel like a combination of:

- a lightweight quantitative research terminal,
- a strategy/backtesting lab,
- a portfolio risk workstation,
- and an AI research assistant.

The product is **not** intended to replace a broker or become an institutional trading platform. It is a personal research and decision-support product that can eventually connect to paper trading.

### Product thesis

> **AI should operate the quantitative system, not invent the quantitative results.**

The deterministic quant engine calculates indicators, signals, simulations, and risk metrics. AI chooses which analyses to run, combines their results, explains them, and helps the user formulate the next research question.

---

## 2. Why We Are Building This

A technically capable individual trader often has to jump between multiple tools for:

- historical price data,
- technical indicators,
- strategy research,
- backtesting,
- portfolio analysis,
- risk analysis,
- options research,
- news/context,
- and trading journals.

The opportunity is to bring those workflows together behind a conversational interface while keeping the underlying analysis reproducible and inspectable.

The product should answer questions such as:

> “Why is my portfolio risk higher this month?”

> “Compare these three strategies on NIFTY.”

> “Find mean-reversion candidates among liquid large-cap stocks.”

> “Would reducing this position improve my portfolio without changing the expected return too much?”

> “Why did this strategy stop working?”

---

## 3. Primary User

### Target user

A technically comfortable individual trader / market researcher who:

- follows equities, indices and potentially derivatives,
- understands basic market terminology,
- wants evidence rather than generic AI predictions,
- wants to experiment with systematic strategies,
- is comfortable running the product locally or as a private single-user service.

### What the user values

1. **Trust** — the numbers must be traceable.
2. **Speed** — common analysis should take seconds, not require manual setup.
3. **Exploration** — the user should be able to move from one question to another naturally.
4. **Learning** — every result should help the user understand the strategy/risk involved.
5. **Control** — no autonomous real-money actions in the initial product.

---

## 4. Product Principles

### 4.1 Quant first, AI second

AI must not fabricate metrics, prices, trade counts, or backtest results. The AI receives structured results from trusted tools.

### 4.2 Every result should be explainable

A user should be able to inspect:

- data period,
- instrument/universe,
- strategy/model,
- parameters,
- transaction-cost assumptions,
- backtest configuration,
- and the calculations used.

### 4.3 Research before automation

The initial product is a **research and decision-support system**. Paper trading comes later. Real-money automation is out of scope for the internship MVP.

### 4.4 Small-user product, not enterprise platform

Do not spend the internship building:

- multi-tenant authorization,
- enterprise billing,
- massive distributed infrastructure,
- institutional market access,
- or HFT infrastructure.

The system should still be designed cleanly enough that those concerns could be added later.

### 4.5 Experiments are first-class objects

A backtest should be reproducible. Save the strategy, parameters, instrument, date range, data version, cost assumptions, and output metrics.

---

## 5. Product Experience

The application should have four main experiences.

### A. Research Workspace

Conversational research area where the user can ask questions.

Example:

> “Analyze RELIANCE for the last three years.”

The system should return:

- price/trend summary,
- volatility,
- selected indicators,
- current signal state,
- market regime,
- relevant historical context,
- and a clear explanation of what the evidence does and does not imply.

### B. Strategy Lab

A workspace for creating and comparing experiments.

Example:

> “Compare MACD, Bollinger Bands and Dual Thrust on NIFTY from 2020 to 2025.”

Show:

- CAGR,
- Sharpe,
- Sortino,
- maximum drawdown,
- win rate,
- profit factor,
- trade count,
- turnover,
- exposure,
- equity curve,
- drawdown curve,
- monthly/rolling performance.

### C. Portfolio & Risk

The user can import or manually define holdings and inspect:

- allocation,
- concentration,
- correlation,
- beta,
- volatility,
- drawdown,
- sector concentration,
- scenario impact.

Example:

> “What happens if I reduce TCS by 20% and increase HDFC Bank?”

The product should compare the portfolio before and after the change.

### D. Trade Journal

Record paper/live trades and analyze performance by:

- strategy,
- market regime,
- asset,
- holding period,
- entry reason,
- exit reason,
- risk discipline.

Example:

> “Why have my last 50 trades underperformed?”

---

## 6. MVP Definition

### Universe

Start with a small, liquid universe:

- NIFTY,
- BANKNIFTY,
- 5–10 large-cap equities.

### Strategy families

Implement four different styles:

1. **MACD / momentum**
2. **Bollinger Bands / mean reversion**
3. **Dual Thrust / breakout**
4. **Pair Trading / statistical arbitrage**

This gives the user different types of systematic thinking rather than four variations of the same strategy.

### MVP capabilities

- historical market-data retrieval,
- indicators,
- reusable strategies,
- realistic backtesting,
- experiment storage,
- strategy comparison,
- portfolio analytics,
- risk analytics,
- conversational research,
- MCP access to the core capabilities.

### Explicitly out of MVP

- autonomous real-money trading,
- high-frequency trading,
- advanced deep-learning price forecasting,
- complex broker routing,
- multi-user access control,
- institutional-grade exchange infrastructure.

---

## 7. AI/MCP Product Model

The AI should behave like a research analyst with access to a toolbox.

Conceptually:

```text
User question
     |
     v
AI Researcher
     |
     v
MCP tools
     |
     +--> market data
     +--> indicators
     +--> backtesting
     +--> portfolio risk
     +--> Monte Carlo
     +--> regime analysis
     +--> paper trading later
     |
     v
Structured quantitative results
     |
     v
AI explanation / next-step recommendation
```

### Example interaction

User:

> “Is my current portfolio too concentrated?”

AI workflow:

```text
get_portfolio()
        |
calculate_exposure()
        |
calculate_correlation()
        |
calculate_beta()
        |
run_risk_analysis()
        |
v
explain findings
```

The user should see the important evidence, not just a conclusion.

---

## 8. User-Facing Product Screens

A polished MVP can have five screens.

### 1. Home / Research

- natural-language input,
- recent questions,
- recent experiments,
- current portfolio snapshot.

### 2. Market View

- selected instrument,
- price chart,
- indicators,
- signal status,
- market regime.

### 3. Strategy Lab

- strategy selector,
- parameter inputs,
- date range,
- cost assumptions,
- Run Backtest,
- comparison table,
- charts.

### 4. Portfolio

- holdings,
- allocation,
- risk metrics,
- correlation matrix,
- scenario analysis.

### 5. Experiments / Journal

- saved backtests,
- trade history,
- performance breakdown,
- AI-generated analysis.

The UI should be clean and functional rather than visually overloaded. Think **professional trading workstation**, not consumer fintech dashboard.

---

## 9. Product Metrics

For a single-user internal product, traditional growth metrics are less useful. Track product usefulness instead.

### Reliability

- percentage of analysis runs completed successfully,
- percentage of numerical outputs passing validation.

### Research efficiency

- time from question to result,
- number of manual steps replaced by the AI workflow.

### Reproducibility

- percentage of experiments that can be rerun from saved configuration.

### User value

- number of research experiments run,
- number of strategies compared,
- number of portfolio reviews,
- number of journal insights generated.

### AI quality

- tool-call accuracy,
- hallucination/error rate on numerical claims,
- percentage of answers with traceable supporting calculations.

---

## 10. Roadmap

### Phase 1 — Foundation

Build the basic research workflow:

```text
data -> indicators -> strategy -> backtest -> metrics
```

Deliverable: one working end-to-end strategy research flow.

### Phase 2 — Strategy Lab

Add all four MVP strategies and experiment storage.

Deliverable: compare strategies reproducibly.

### Phase 3 — Portfolio & Risk

Add portfolio analytics, correlation, volatility, drawdown, and scenario analysis.

Deliverable: portfolio risk workstation.

### Phase 4 — MCP

Expose the core product capabilities as AI-accessible tools.

Deliverable: external MCP client can research the system without using the web UI.

### Phase 5 — AI Researcher

Teach the model to plan multi-step analysis.

Deliverable:

> User asks one question → AI selects tools → quant engine runs → AI synthesizes result.

### Phase 6 — Regime & Advanced Research

Add regime detection, Monte Carlo, statistical screening, and later options research.

### Phase 7 — Paper Trading

Only after the previous layers are stable:

```text
signal -> risk check -> paper order -> journal -> feedback
```

---

## 11. What Would Make the Final Product Impressive in an Interview

The final demo should show an actual research workflow, not a collection of screens.

### Demo scenario

Start with:

> “Analyze my portfolio and identify the biggest risk.”

Then:

> “Find strategies that have historically worked better in the current market regime.”

Then:

> “Backtest those strategies with transaction costs and compare them.”

Then:

> “Would changing my portfolio allocation reduce risk?”

Then:

> “Save this as an experiment.”

Finally:

> “Show me exactly which data and assumptions were used.”

The demo should visibly show the transition:

```text
Natural-language question
        ↓
AI reasoning
        ↓
MCP tool calls
        ↓
Quantitative calculation
        ↓
Backtest / risk analysis
        ↓
Visual result
        ↓
Explainable conclusion
```

That is the product story.

---

## 12. Product Acceptance Criteria

The MVP is complete only when all of the following work end-to-end:

- A user can select an instrument and date range.
- A user can run at least four strategy types.
- A backtest includes transaction costs and slippage assumptions.
- Backtest results include both returns and risk metrics.
- Experiments can be saved and rerun.
- Portfolio risk can be calculated from holdings.
- The AI can invoke the research tools through MCP.
- AI-generated numerical claims map back to tool outputs.
- A user can inspect the assumptions behind a result.
- The system can run entirely without placing real-money orders.

---

## 13. Intern Deliverable

By the end of the internship, the intern should be able to demonstrate a working product with:

1. a usable research UI,
2. a reusable quant/backtesting core,
3. portfolio/risk analytics,
4. MCP-accessible capabilities,
5. an AI research workflow,
6. saved/reproducible experiments,
7. automated tests for important calculations,
8. documentation explaining architecture and research assumptions.

The project is successful when an interviewer can ask a new trading-research question in natural language and watch the system **reason, call tools, calculate the answer, and explain the evidence**.
