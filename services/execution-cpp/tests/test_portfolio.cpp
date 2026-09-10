// Portfolio accounting: open / add / reduce / reverse, trade records and cost
// attribution.
//
// Fills are hand-built here rather than produced by the simulator, so a failure
// points at the accounting rule and not at the bar loop. Most cases use a fill
// price equal to the reference price and no commission, which makes every
// expectation an exact integer; cost attribution gets its own case where the
// costs are the thing under test.

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "quant/portfolio.hpp"

using Catch::Matchers::WithinRel;
using quant::FillEvent;
using quant::OrderSide;
using quant::Portfolio;

namespace {

//: A frictionless fill: no commission, and no gap between reference and fill.
FillEvent plain(OrderSide side, double quantity, double price, std::size_t bar,
                const std::string& reason = "") {
  FillEvent fill;
  fill.bar_index = bar;
  fill.side = side;
  fill.quantity = quantity;
  fill.reference_price = price;
  fill.fill_price = price;
  fill.commission = 0.0;
  fill.reason = reason;
  return fill;
}

}  // namespace

TEST_CASE("a portfolio must start with real money", "[portfolio]") {
  REQUIRE_THROWS_AS(Portfolio(0.0), std::invalid_argument);
  REQUIRE_THROWS_AS(Portfolio(-1.0), std::invalid_argument);
}

TEST_CASE("opening a position sets the average price and spends cash", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 100.0, 3, "entry"), 3);

  REQUIRE_THAT(portfolio.cash(), WithinRel(999000.0, 1e-12));
  REQUIRE_THAT(portfolio.position().quantity, WithinRel(10.0, 1e-12));
  REQUIRE_THAT(portfolio.position().average_price, WithinRel(100.0, 1e-12));
  REQUIRE(portfolio.position().entry_bar_index == 3);
  REQUIRE(portfolio.position().entry_reason == "entry");
  REQUIRE(portfolio.trades().empty());
  REQUIRE_THAT(portfolio.total_traded_notional(), WithinRel(1000.0, 1e-12));
}

TEST_CASE("adding to a position blends the average price", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 100.0, 0), 0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 120.0, 1), 1);

  REQUIRE_THAT(portfolio.position().quantity, WithinRel(20.0, 1e-12));
  REQUIRE_THAT(portfolio.position().average_price, WithinRel(110.0, 1e-12));
  // Adding realizes nothing: no trade has completed.
  REQUIRE(portfolio.trades().empty());
  REQUIRE(portfolio.realized_pnl() == 0.0);
}

TEST_CASE("reducing realizes PnL on the closed quantity only", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 20.0, 110.0, 0), 0);
  portfolio.apply_fill(plain(OrderSide::Sell, 5.0, 130.0, 4, "take profit"), 4);

  REQUIRE_THAT(portfolio.position().quantity, WithinRel(15.0, 1e-12));
  // The average price of what is still held does not move when part is sold.
  REQUIRE_THAT(portfolio.position().average_price, WithinRel(110.0, 1e-12));
  REQUIRE_THAT(portfolio.realized_pnl(), WithinRel(100.0, 1e-12));

  REQUIRE(portfolio.trades().size() == 1);
  const quant::Trade& trade = portfolio.trades().front();
  REQUIRE(trade.direction == 1);
  REQUIRE_THAT(trade.quantity, WithinRel(5.0, 1e-12));
  REQUIRE_THAT(trade.gross_pnl, WithinRel(100.0, 1e-12));
  REQUIRE(trade.costs == 0.0);
  REQUIRE_THAT(trade.pnl, WithinRel(100.0, 1e-12));
  REQUIRE_THAT(trade.return_pct, WithinRel(100.0 / 550.0, 1e-12));
  REQUIRE(trade.bars_held == 4);
  REQUIRE(trade.exit_reason == "take profit");
}

TEST_CASE("a short round trip profits when the price falls", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Sell, 10.0, 100.0, 0), 0);
  // Short proceeds are credited: this engine models a cash account, not margin.
  REQUIRE_THAT(portfolio.cash(), WithinRel(1001000.0, 1e-12));
  REQUIRE_THAT(portfolio.position().quantity, WithinRel(-10.0, 1e-12));

  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 90.0, 2), 2);
  REQUIRE(portfolio.position().quantity == 0.0);
  REQUIRE_THAT(portfolio.realized_pnl(), WithinRel(100.0, 1e-12));
  REQUIRE(portfolio.trades().front().direction == -1);
}

TEST_CASE("a reversal closes fully and re-opens at the fill price", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 100.0, 0), 0);
  portfolio.apply_fill(plain(OrderSide::Sell, 25.0, 90.0, 5), 5);

  // The 10 long units are closed at 90 for a 100 loss, and the remaining 15
  // open a short. The average price is the fill price, NOT a blend of 100 and
  // 90 — a blended price is one that never existed in the market.
  REQUIRE(portfolio.trades().size() == 1);
  REQUIRE_THAT(portfolio.trades().front().gross_pnl, WithinRel(-100.0, 1e-12));
  REQUIRE_THAT(portfolio.position().quantity, WithinRel(-15.0, 1e-12));
  REQUIRE_THAT(portfolio.position().average_price, WithinRel(90.0, 1e-12));
  REQUIRE(portfolio.position().entry_bar_index == 5);
}

TEST_CASE("costs are attributed to both legs of a trade", "[portfolio]") {
  Portfolio portfolio(1000000.0);

  FillEvent entry;
  entry.bar_index = 0;
  entry.side = OrderSide::Buy;
  entry.quantity = 10.0;
  entry.reference_price = 100.0;
  entry.fill_price = 101.0;
  entry.commission = 5.0;
  portfolio.apply_fill(entry, 0);  // cost = 1.0 * 10 slippage + 5 commission = 15

  FillEvent exit;
  exit.bar_index = 6;
  exit.side = OrderSide::Sell;
  exit.quantity = 5.0;
  exit.reference_price = 100.0;
  exit.fill_price = 99.0;
  exit.commission = 2.0;
  portfolio.apply_fill(exit, 6);  // cost = 1.0 * 5 slippage + 2 commission = 7

  const quant::Trade& trade = portfolio.trades().front();
  // Half the position is closed, so half of the entry's 15 comes with it, plus
  // all 7 of the exit's cost — the exit fill closed nothing else.
  REQUIRE_THAT(trade.costs, WithinRel(7.5 + 7.0, 1e-12));
  REQUIRE_THAT(trade.gross_pnl, WithinRel(-10.0, 1e-12));
  REQUIRE_THAT(trade.pnl, WithinRel(-24.5, 1e-12));
  // The other half of the entry cost stays attached to what is still open.
  REQUIRE_THAT(portfolio.position().open_costs, WithinRel(7.5, 1e-12));
  REQUIRE_THAT(portfolio.total_commission(), WithinRel(7.0, 1e-12));
  REQUIRE_THAT(portfolio.total_slippage(), WithinRel(15.0, 1e-12));
}

TEST_CASE("closing the last unit clears the position completely", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 100.0, 0, "entry"), 0);
  portfolio.apply_fill(plain(OrderSide::Sell, 10.0, 105.0, 1), 1);

  REQUIRE(portfolio.position().quantity == 0.0);
  REQUIRE(portfolio.position().average_price == 0.0);
  REQUIRE(portfolio.position().open_costs == 0.0);
  REQUIRE(portfolio.position().entry_reason.empty());
  REQUIRE(portfolio.position().is_flat());
  // Realized PnL survives the flatten; it is the run's record, not the
  // position's.
  REQUIRE_THAT(portfolio.realized_pnl(), WithinRel(50.0, 1e-12));
}

TEST_CASE("unrealized PnL is marked against the current price", "[portfolio]") {
  Portfolio portfolio(1000000.0);
  portfolio.apply_fill(plain(OrderSide::Buy, 10.0, 100.0, 0), 0);
  REQUIRE_THAT(portfolio.position().unrealized_pnl(115.0), WithinRel(150.0, 1e-12));
  REQUIRE_THAT(portfolio.equity(115.0), WithinRel(999000.0 + 1150.0, 1e-12));

  Portfolio shorted(1000000.0);
  shorted.apply_fill(plain(OrderSide::Sell, 10.0, 100.0, 0), 0);
  // A short gains when the price falls.
  REQUIRE_THAT(shorted.position().unrealized_pnl(90.0), WithinRel(100.0, 1e-12));
}

TEST_CASE("only buys are constrained by cash", "[portfolio]") {
  Portfolio portfolio(1000.0);
  REQUIRE(portfolio.can_afford(OrderSide::Buy, 9.0, 100.0, 0.5));
  REQUIRE_FALSE(portfolio.can_afford(OrderSide::Buy, 20.0, 100.0, 0.5));
  // Selling always passes: closing a long returns cash and opening a short
  // receives proceeds.
  REQUIRE(portfolio.can_afford(OrderSide::Sell, 1000000.0, 100.0, 0.5));
}

TEST_CASE("the affordable quantity is a whole number including commission",
          "[portfolio]") {
  Portfolio portfolio(1000.0);
  // 1000 / (100 * 1.0003) = 9.997, and a partial unit is not tradable.
  REQUIRE_THAT(portfolio.max_affordable_quantity(100.0, 0.0003), WithinRel(9.0, 1e-12));
  // Exactly ten units are affordable with no commission.
  REQUIRE_THAT(portfolio.max_affordable_quantity(100.0, 0.0), WithinRel(10.0, 1e-12));
  // Nothing is affordable at a price above the whole balance.
  REQUIRE(portfolio.max_affordable_quantity(5000.0, 0.0) == 0.0);
  REQUIRE(portfolio.max_affordable_quantity(0.0, 0.0) == 0.0);
  REQUIRE(portfolio.max_affordable_quantity(-1.0, 0.0) == 0.0);
}
