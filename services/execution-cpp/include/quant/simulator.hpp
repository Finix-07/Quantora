// The event-driven execution simulator (architecture.md §7, milestone M4).
//
// THE PARITY SURFACE
// ------------------
// The Python `simulate()` in services/quant/backtest/engine.py does two things:
// it derives a per-bar *target position* (which needs the strategy, indicators
// and sizing context) and it *executes* that target against the following bar
// with costs, a cash constraint and portfolio accounting. Only the second half
// is a closed numeric problem, so only the second half lives here:
//
//     in : bars, one target quantity per bar, a cost model, a config
//     out: equity curve, exposure, orders, fills, positions, trades,
//          final cash/position and the cost totals
//
// `engine.simulate_targets()` solves exactly this problem in Python and is what
// the M4.5 parity test compares against, bar for bar.
//
// The look-ahead guarantee (NFR5.3) is structural, not conventional: a decision
// taken at bar *t* is parked in a single-slot pending-order queue and can only
// be filled when bar *t+1* is reached. There is no code path that fills on the
// deciding bar.

#ifndef QUANT_SIMULATOR_HPP
#define QUANT_SIMULATOR_HPP

#include <cstddef>
#include <string>
#include <vector>

#include "quant/costs.hpp"
#include "quant/events.hpp"
#include "quant/portfolio.hpp"

namespace quant {

//: Which bar price an order is benchmarked against on its execution bar.
//
// Both options fill on a *later* bar than the one that produced the decision;
// same-bar-close execution is deliberately not offered (see config.py).
enum class PriceField : int {
  Open = 0,
  Close = 1,
};

struct SimulationConfig {
  double initial_cash = 1000000.0;
  CostModel costs{};
  PriceField price_field = PriceField::Open;
  //: Close any open position at the final bar's close, paying costs. Without
  //: it, a strategy holding a winner at the end would book an unrealised gain
  //: as though it had been cashed out for free.
  bool liquidate_at_end = true;
};

struct Bar {
  double open = 0.0;
  double high = 0.0;
  double low = 0.0;
  double close = 0.0;
  double volume = 0.0;
};

//: A fill the cash constraint had to interfere with.
//
// Reported as facts rather than as a sentence: the wording lives in
// engine.py (`skipped_buy_warning` / `reduced_buy_warning`) and the binding
// renders it, so the two engines physically cannot describe the same event
// differently.
struct ExecutionWarning {
  enum class Kind : int {
    SkippedBuy = 0,
    ReducedBuy = 1,
  };

  std::size_t bar_index = 0;
  Kind kind = Kind::SkippedBuy;
  //: What the order asked for.
  double requested_quantity = 0.0;
  //: What cash allowed. Zero for a skipped buy.
  double filled_quantity = 0.0;
  double fill_price = 0.0;
};

struct SimulationResult {
  std::vector<double> equity_curve;
  std::vector<double> exposure;
  std::vector<OrderEvent> orders;
  std::vector<FillEvent> fills;
  std::vector<PositionEvent> positions;
  std::vector<Trade> trades;
  std::vector<ExecutionWarning> warnings;

  //: The position as it stands after the run, including the entry-side costs
  //: still attached to it — the binding needs all of it to rebuild an
  //: equivalent Python Portfolio for the parity comparison.
  PositionState final_position;

  double final_cash = 0.0;
  double final_quantity = 0.0;
  double realized_pnl = 0.0;
  double total_commission = 0.0;
  double total_slippage = 0.0;
  double total_traded_notional = 0.0;
};

//: Execute a pre-computed per-bar target position.
//
// `targets[i]` is the position to hold *from bar i+1 onwards*, decided at bar
// `i`'s close; the last entry is therefore never acted on. `reasons` may be
// empty, in which case every order carries an empty reason.
//
// Throws std::invalid_argument on an empty bar series or a length mismatch —
// silently padding would make the results wrong in a way nothing would notice.
SimulationResult simulate_targets(const std::vector<Bar>& bars,
                                  const std::vector<double>& targets,
                                  const std::vector<std::string>& reasons,
                                  const SimulationConfig& config);

}  // namespace quant

#endif  // QUANT_SIMULATOR_HPP
