// The bar loop: event sequencing, the single-slot pending-order queue, the cash
// constraint and end-of-run liquidation.
//
// Every scenario here is short enough to compute by hand, which is the point —
// a simulator checked only against its own previous output cannot tell you it
// was ever right.

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <string>
#include <vector>

#include "quant/simulator.hpp"

using Catch::Matchers::WithinRel;
using quant::Bar;
using quant::CostModel;
using quant::ExecutionWarning;
using quant::OrderSide;
using quant::SimulationConfig;
using quant::SimulationResult;

namespace {

//: Bars whose open is the previous close, so a price path is one list.
std::vector<Bar> path(const std::vector<double>& closes) {
  std::vector<Bar> bars;
  bars.reserve(closes.size());
  for (std::size_t i = 0; i < closes.size(); ++i) {
    const double open = i == 0 ? closes[0] : closes[i - 1];
    const double high = (open > closes[i] ? open : closes[i]) * 1.01;
    const double low = (open < closes[i] ? open : closes[i]) * 0.99;
    bars.push_back(Bar{open, high, low, closes[i], 1000000.0});
  }
  return bars;
}

SimulationConfig frictionless(double cash = 1000000.0) {
  SimulationConfig config;
  config.initial_cash = cash;
  config.costs = CostModel::zero();
  return config;
}

const std::vector<std::string> kNoReasons;

}  // namespace

TEST_CASE("the simulator refuses malformed input rather than padding it", "[simulator]") {
  REQUIRE_THROWS_AS(quant::simulate_targets({}, {}, kNoReasons, frictionless()),
                    std::invalid_argument);
  REQUIRE_THROWS_AS(quant::simulate_targets(path({100.0, 101.0}), {1.0}, kNoReasons, frictionless()),
                    std::invalid_argument);
  REQUIRE_THROWS_AS(
      quant::simulate_targets(path({100.0, 101.0}), {1.0, 1.0}, {"only one"}, frictionless()),
      std::invalid_argument);
}

TEST_CASE("a decision at one bar can only fill at the next", "[simulator][lookahead]") {
  // The non-negotiable guarantee (NFR5.3). Asserted structurally rather than
  // trusted: every order's execution bar is strictly after its decision bar,
  // and every fill lands on the bar its order was scheduled for.
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 110.0, 105.0, 130.0, 90.0, 95.0}), {10.0, 10.0, 0.0, -5.0, -5.0, 0.0},
      kNoReasons, frictionless());

  REQUIRE_FALSE(result.orders.empty());
  for (const quant::OrderEvent& order : result.orders) {
    REQUIRE(order.decision_bar < order.bar_index);
    REQUIRE(order.bar_index == order.decision_bar + 1);
  }
  // Bar 0 can never see a fill: nothing has been decided yet.
  for (const quant::FillEvent& fill : result.fills) {
    REQUIRE(fill.bar_index > 0);
  }
}

TEST_CASE("events are emitted in Market -> Order -> Fill -> Position order",
          "[simulator][events]") {
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 110.0, 120.0}), {10.0, 10.0, 0.0}, kNoReasons, frictionless());

  // One position event per bar, in bar order — the mark-to-market record is
  // complete, so a metric computed from it cannot silently skip a bar.
  REQUIRE(result.positions.size() == 3);
  REQUIRE(result.equity_curve.size() == 3);
  REQUIRE(result.exposure.size() == 3);
  for (std::size_t i = 0; i < result.positions.size(); ++i) {
    REQUIRE(result.positions[i].bar_index == i);
  }

  // The fill that follows an order is recorded at the order's execution bar,
  // and the position event for that bar already reflects it.
  REQUIRE(result.orders.front().decision_bar == 0);
  REQUIRE(result.fills.front().bar_index == 1);
  REQUIRE_THAT(result.positions[1].quantity, WithinRel(10.0, 1e-12));
  REQUIRE(result.positions[0].quantity == 0.0);
}

TEST_CASE("a frictionless long round trip is exactly hand-computable", "[simulator]") {
  // Bars: closes 100, 110, 120; opens 100, 100, 110.
  // Bar 0 decides target 10 -> buys 10 at bar 1's open of 100.
  // Liquidation sells 10 at bar 2's close of 120.
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 110.0, 120.0}), {10.0, 10.0, 0.0}, kNoReasons, frictionless());

  REQUIRE(result.fills.size() == 2);
  REQUIRE(result.fills[0].side == OrderSide::Buy);
  REQUIRE_THAT(result.fills[0].fill_price, WithinRel(100.0, 1e-12));
  REQUIRE(result.fills[1].side == OrderSide::Sell);
  REQUIRE_THAT(result.fills[1].fill_price, WithinRel(120.0, 1e-12));

  REQUIRE_THAT(result.equity_curve[0], WithinRel(1000000.0, 1e-12));
  REQUIRE_THAT(result.equity_curve[1], WithinRel(1000100.0, 1e-12));  // 999000 + 10*110
  REQUIRE_THAT(result.equity_curve[2], WithinRel(1000200.0, 1e-12));  // liquidated at 120
  REQUIRE(result.exposure[0] == 0.0);
  REQUIRE_THAT(result.exposure[1], WithinRel(1100.0 / 1000100.0, 1e-12));
  // Exposure is forced to zero at the end when the position is closed out.
  REQUIRE(result.exposure[2] == 0.0);

  REQUIRE(result.trades.size() == 1);
  REQUIRE_THAT(result.trades[0].pnl, WithinRel(200.0, 1e-12));
  REQUIRE(result.trades[0].bars_held == 1);
  REQUIRE_THAT(result.final_cash, WithinRel(1000200.0, 1e-12));
  REQUIRE(result.final_quantity == 0.0);
  REQUIRE_THAT(result.total_traded_notional, WithinRel(1000.0 + 1200.0, 1e-12));
}

TEST_CASE("costs move the fill away from the reference price on both sides",
          "[simulator][costs]") {
  SimulationConfig config;
  config.initial_cash = 1000000.0;
  config.costs = CostModel{3.0, 0.0, 5.0, 2.0};  // 6 bps of price penalty per side

  const SimulationResult result = quant::simulate_targets(
      path({100.0, 100.0, 100.0}), {10.0, 10.0, 0.0}, kNoReasons, config);

  REQUIRE(result.fills.size() == 2);
  REQUIRE_THAT(result.fills[0].fill_price, WithinRel(100.06, 1e-12));
  REQUIRE_THAT(result.fills[1].fill_price, WithinRel(99.94, 1e-12));
  REQUIRE_THAT(result.total_slippage, WithinRel(0.6 + 0.6, 1e-9));
  REQUIRE_THAT(result.total_commission,
               WithinRel(1000.6 * 0.0003 + 999.4 * 0.0003, 1e-12));
  // A flat market is a loss once both sides are charged. A cost model that
  // penalised only the entry would show this round trip as roughly break-even.
  REQUIRE(result.trades[0].pnl < 0.0);
  REQUIRE(result.trades[0].gross_pnl < 0.0);
}

TEST_CASE("an unaffordable buy is reduced to the affordable whole quantity",
          "[simulator][cash]") {
  SimulationConfig config;
  config.initial_cash = 1000.0;
  config.costs = CostModel{3.0, 0.0, 0.0, 0.0};  // commission only, so the price is 100
  config.liquidate_at_end = false;

  // Two bars only: the point is the constrained fill itself, and a third bar
  // would simply retry the unfilled remainder and skip it for want of cash.
  const SimulationResult result =
      quant::simulate_targets(path({100.0, 100.0}), {20.0, 20.0}, kNoReasons, config);

  // 1000 / (100 * 1.0003) = 9.997 -> 9 whole units.
  REQUIRE(result.warnings.size() == 1);
  REQUIRE(result.warnings[0].kind == ExecutionWarning::Kind::ReducedBuy);
  REQUIRE(result.warnings[0].bar_index == 1);
  REQUIRE_THAT(result.warnings[0].requested_quantity, WithinRel(20.0, 1e-12));
  REQUIRE_THAT(result.warnings[0].filled_quantity, WithinRel(9.0, 1e-12));

  REQUIRE(result.fills.size() == 1);
  REQUIRE_THAT(result.fills[0].quantity, WithinRel(9.0, 1e-12));
  REQUIRE_THAT(result.final_quantity, WithinRel(9.0, 1e-12));
  // Cash never goes negative: that is the whole point of the constraint.
  REQUIRE(result.final_cash >= 0.0);
  REQUIRE_THAT(result.final_cash, WithinRel(1000.0 - 900.0 - 0.27, 1e-12));
}

TEST_CASE("a buy nothing can pay for is skipped, not partially imagined",
          "[simulator][cash]") {
  SimulationConfig config;
  config.initial_cash = 50.0;
  config.costs = CostModel::zero();
  config.liquidate_at_end = false;

  const SimulationResult result =
      quant::simulate_targets(path({100.0, 100.0}), {5.0, 5.0}, kNoReasons, config);

  REQUIRE(result.warnings.size() == 1);
  REQUIRE(result.warnings[0].kind == ExecutionWarning::Kind::SkippedBuy);
  REQUIRE(result.warnings[0].filled_quantity == 0.0);
  REQUIRE(result.fills.empty());
  REQUIRE(result.final_quantity == 0.0);
  REQUIRE_THAT(result.final_cash, WithinRel(50.0, 1e-12));
}

TEST_CASE("a reversal closes and re-opens through the bar loop", "[simulator]") {
  SimulationConfig config = frictionless();
  config.liquidate_at_end = false;

  // Long 10 from bar 1, then target -5 decided at bar 2 -> a single sell of 15.
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 100.0, 100.0, 90.0, 90.0}), {10.0, 10.0, -5.0, -5.0, -5.0}, kNoReasons, config);

  REQUIRE(result.fills.size() == 2);
  REQUIRE(result.fills[1].side == OrderSide::Sell);
  REQUIRE_THAT(result.fills[1].quantity, WithinRel(15.0, 1e-12));

  REQUIRE(result.trades.size() == 1);
  REQUIRE(result.trades[0].direction == 1);
  REQUIRE_THAT(result.trades[0].quantity, WithinRel(10.0, 1e-12));
  REQUIRE_THAT(result.final_quantity, WithinRel(-5.0, 1e-12));
  // Re-opened at the fill price of the reversing leg, not a blend.
  REQUIRE_THAT(result.positions.back().average_price, WithinRel(100.0, 1e-12));
}

TEST_CASE("liquidation can be turned off, and then the position is left open",
          "[simulator]") {
  const std::vector<Bar> bars = path({100.0, 110.0, 120.0});
  const std::vector<double> targets{10.0, 10.0, 10.0};

  SimulationConfig closing = frictionless();
  const SimulationResult liquidated =
      quant::simulate_targets(bars, targets, kNoReasons, closing);

  SimulationConfig holding = frictionless();
  holding.liquidate_at_end = false;
  const SimulationResult held = quant::simulate_targets(bars, targets, kNoReasons, holding);

  REQUIRE(liquidated.final_quantity == 0.0);
  REQUIRE(liquidated.trades.size() == 1);
  REQUIRE_THAT(held.final_quantity, WithinRel(10.0, 1e-12));
  REQUIRE(held.trades.empty());
  // Frictionless, so closing out costs nothing and the two end at the same
  // equity — the difference is whether the gain is realized, not its size.
  REQUIRE_THAT(liquidated.equity_curve.back(), WithinRel(held.equity_curve.back(), 1e-12));
  // The exposure series differs precisely because one run is flat at the end.
  REQUIRE(liquidated.exposure.back() == 0.0);
  REQUIRE(held.exposure.back() != 0.0);
}

TEST_CASE("a run that never trades still produces a full flat record", "[simulator]") {
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 110.0, 120.0, 130.0}), {0.0, 0.0, 0.0, 0.0}, kNoReasons, frictionless());

  REQUIRE(result.orders.empty());
  REQUIRE(result.fills.empty());
  REQUIRE(result.trades.empty());
  REQUIRE(result.warnings.empty());
  REQUIRE(result.equity_curve.size() == 4);
  for (const double equity : result.equity_curve) {
    REQUIRE(equity == 1000000.0);
  }
  for (const double exposure : result.exposure) {
    REQUIRE(exposure == 0.0);
  }
}

TEST_CASE("orders below a whole unit of noise are not placed", "[simulator]") {
  SimulationConfig config = frictionless();
  config.liquidate_at_end = false;
  // A target that drifts by 1e-12 is numerical noise, not a trading decision;
  // acting on it would produce a fill and a commission out of nothing.
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 100.0, 100.0}), {10.0, 10.0 + 1e-12, 10.0}, kNoReasons, config);

  REQUIRE(result.orders.size() == 1);
  REQUIRE(result.fills.size() == 1);
}

TEST_CASE("order reasons reach the trade record", "[simulator]") {
  const std::vector<std::string> reasons{"go long", "hold", "close out"};
  const SimulationResult result = quant::simulate_targets(
      path({100.0, 110.0, 120.0}), {10.0, 10.0, 0.0}, reasons, frictionless());

  REQUIRE(result.orders.front().reason == "go long");
  REQUIRE(result.fills.front().reason == "go long");
  REQUIRE(result.trades.front().entry_reason == "go long");
  // The final trade is closed by the end-of-run liquidation, which names itself
  // rather than borrowing the last signal's reason.
  REQUIRE(result.trades.front().exit_reason.rfind("backtest ended", 0) == 0);
}
