// Unit tests for the event vocabulary and the cost model.
//
// These are the smallest pieces and the ones every other number is built from:
// a wrong sign in `cash_delta` or a half-spread charged once instead of twice
// would corrupt every equity curve the engine ever produces, while still
// leaving the simulator's structure looking correct.

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "quant/costs.hpp"
#include "quant/events.hpp"

using Catch::Matchers::WithinRel;
using quant::CostModel;
using quant::FillEvent;
using quant::OrderSide;
using quant::PositionEvent;

TEST_CASE("order sides carry the arithmetic sign convention", "[events]") {
  REQUIRE(quant::side_sign(OrderSide::Buy) == 1);
  REQUIRE(quant::side_sign(OrderSide::Sell) == -1);
}

TEST_CASE("a buy spends cash and a sell receives it", "[events]") {
  FillEvent buy;
  buy.side = OrderSide::Buy;
  buy.quantity = 10.0;
  buy.reference_price = 100.0;
  buy.fill_price = 100.5;
  buy.commission = 3.0;

  REQUIRE_THAT(buy.notional(), WithinRel(1005.0, 1e-12));
  // Both legs of the cost are charged: the 0.5 of slippage per unit and the
  // flat commission. A fill that only booked one of them would understate the
  // round-trip drag by half.
  REQUIRE_THAT(buy.slippage_cost(), WithinRel(5.0, 1e-12));
  REQUIRE_THAT(buy.total_cost(), WithinRel(8.0, 1e-12));
  REQUIRE_THAT(buy.cash_delta(), WithinRel(-1008.0, 1e-12));

  FillEvent sell = buy;
  sell.side = OrderSide::Sell;
  sell.fill_price = 99.5;
  REQUIRE_THAT(sell.slippage_cost(), WithinRel(5.0, 1e-12));
  // Selling receives the notional but still pays commission.
  REQUIRE_THAT(sell.cash_delta(), WithinRel(995.0 - 3.0, 1e-12));
}

TEST_CASE("slippage is measured against the reference price, not zero", "[events]") {
  FillEvent fill;
  fill.side = OrderSide::Sell;
  fill.quantity = 4.0;
  fill.reference_price = 250.0;
  fill.fill_price = 250.0;
  REQUIRE(fill.slippage_cost() == 0.0);
}

TEST_CASE("position events report equity as cash plus market value", "[events]") {
  const PositionEvent event{7, -3.0, 100.0, 5000.0, -270.0, 90.0, 12.0};
  REQUIRE_THAT(event.equity(), WithinRel(4730.0, 1e-12));
}

TEST_CASE("both sides of a trade are penalised", "[costs]") {
  const CostModel costs{3.0, 0.0, 5.0, 2.0};
  // 5 bps of slippage plus half of a 2 bps spread = 6 bps, paid up when buying
  // and down when selling.
  REQUIRE_THAT(costs.fill_price(100.0, 1), WithinRel(100.06, 1e-12));
  REQUIRE_THAT(costs.fill_price(100.0, -1), WithinRel(99.94, 1e-12));
  REQUIRE_THROWS_AS(costs.fill_price(100.0, 0), std::invalid_argument);
}

TEST_CASE("commission has a floor and ignores the sign of the notional", "[costs]") {
  const CostModel costs{3.0, 20.0, 0.0, 0.0};
  // 3 bps of 10,000 is 3, below the 20 minimum.
  REQUIRE_THAT(costs.commission(10000.0), WithinRel(20.0, 1e-12));
  REQUIRE_THAT(costs.commission(-10000.0), WithinRel(20.0, 1e-12));
  // 3 bps of 1,000,000 is 300, above it.
  REQUIRE_THAT(costs.commission(1000000.0), WithinRel(300.0, 1e-12));
}

TEST_CASE("the zero cost model is frictionless", "[costs]") {
  const CostModel costs = CostModel::zero();
  REQUIRE(costs.fill_price(123.45, 1) == 123.45);
  REQUIRE(costs.fill_price(123.45, -1) == 123.45);
  REQUIRE(costs.commission(1e9) == 0.0);
}
