#include "quant/simulator.hpp"

#include <cmath>
#include <optional>
#include <stdexcept>
#include <utility>

namespace quant {
namespace {

//: An order below this size is not worth placing; mirrors `_pending_order`.
constexpr double kMinimumOrderQuantity = 1e-9;

std::optional<OrderEvent> make_pending_order(std::size_t decision_bar, std::size_t execution_bar,
                                             double delta, const std::string& reason) {
  if (std::fabs(delta) < kMinimumOrderQuantity) return std::nullopt;
  OrderEvent order;
  order.decision_bar = decision_bar;
  order.bar_index = execution_bar;
  order.side = delta > 0.0 ? OrderSide::Buy : OrderSide::Sell;
  order.quantity = std::fabs(delta);
  order.reason = reason;
  return order;
}

double reference_price_of(const MarketEvent& market, PriceField field) {
  return field == PriceField::Open ? market.open : market.close;
}

//: Turn an order into a fill, honouring costs and the cash constraint.
std::optional<FillEvent> execute(const OrderEvent& order, const MarketEvent& market,
                                 PriceField price_field, const Portfolio& portfolio,
                                 const SimulationConfig& config,
                                 std::vector<ExecutionWarning>& warnings) {
  if (order.bar_index != market.bar_index) {
    // Defensive: the queue is single-slot, so this can only happen if the loop
    // is changed incorrectly. Failing loudly beats filling on the wrong bar,
    // which is precisely a look-ahead bug.
    throw std::logic_error("order scheduled for one bar reached a different bar");
  }

  const double reference_price = reference_price_of(market, price_field);
  const int side = side_sign(order.side);
  const double fill_price = config.costs.fill_price(reference_price, side);
  double quantity = order.quantity;
  double commission = config.costs.commission(quantity * fill_price);

  if (order.side == OrderSide::Buy &&
      !portfolio.can_afford(order.side, quantity, fill_price, commission)) {
    const double affordable = portfolio.max_affordable_quantity(
        fill_price, config.costs.commission_bps / 10000.0);
    if (affordable <= 0.0) {
      warnings.push_back(ExecutionWarning{market.bar_index, ExecutionWarning::Kind::SkippedBuy,
                                          quantity, 0.0, fill_price});
      return std::nullopt;
    }
    warnings.push_back(ExecutionWarning{market.bar_index, ExecutionWarning::Kind::ReducedBuy,
                                        quantity, affordable, fill_price});
    quantity = affordable;
    commission = config.costs.commission(quantity * fill_price);
  }

  FillEvent fill;
  fill.bar_index = market.bar_index;
  fill.side = order.side;
  fill.quantity = quantity;
  fill.reference_price = reference_price;
  fill.fill_price = fill_price;
  fill.commission = commission;
  fill.reason = order.reason;
  return fill;
}

void liquidate(const std::vector<Bar>& bars, Portfolio& portfolio, const SimulationConfig& config,
               SimulationResult& result) {
  const PositionState& position = portfolio.position();
  if (position.is_flat()) return;

  const std::size_t last_index = bars.size() - 1;
  const OrderSide side = position.quantity > 0.0 ? OrderSide::Sell : OrderSide::Buy;

  FillEvent fill;
  fill.bar_index = last_index;
  fill.side = side;
  fill.quantity = std::fabs(position.quantity);
  fill.reference_price = bars[last_index].close;
  fill.fill_price = config.costs.fill_price(fill.reference_price, side_sign(side));
  fill.commission = config.costs.commission(fill.quantity * fill.fill_price);
  fill.reason = "backtest ended — position closed at the final close";

  portfolio.apply_fill(fill, last_index);
  result.fills.push_back(std::move(fill));
}

}  // namespace

SimulationResult simulate_targets(const std::vector<Bar>& bars, const std::vector<double>& targets,
                                  const std::vector<std::string>& reasons,
                                  const SimulationConfig& config) {
  if (bars.empty()) {
    throw std::invalid_argument("cannot simulate a backtest over an empty price series");
  }
  if (targets.size() != bars.size()) {
    throw std::invalid_argument(
        "targets must have exactly one entry per bar");
  }
  if (!reasons.empty() && reasons.size() != bars.size()) {
    throw std::invalid_argument("reasons must be empty or have exactly one entry per bar");
  }

  const std::size_t n = bars.size();
  Portfolio portfolio(config.initial_cash);

  SimulationResult result;
  result.equity_curve.reserve(n);
  result.exposure.reserve(n);
  result.positions.reserve(n);

  static const std::string kNoReason;

  // Orders decided on the previous bar, awaiting execution on this one. This
  // single-slot queue is the mechanism that enforces the causality guarantee:
  // nothing can be placed and filled within the same bar.
  std::optional<OrderEvent> pending;

  for (std::size_t i = 0; i < n; ++i) {
    const Bar& bar = bars[i];
    const MarketEvent market{i, bar.open, bar.high, bar.low, bar.close, bar.volume};

    // --- 1. Execute the order decided on the previous bar --------------------
    if (pending.has_value()) {
      std::optional<FillEvent> fill =
          execute(*pending, market, config.price_field, portfolio, config, result.warnings);
      if (fill.has_value()) {
        portfolio.apply_fill(*fill, i);
        result.fills.push_back(std::move(*fill));
      }
      pending.reset();
    }

    // --- 2. Mark to market at this bar's close -------------------------------
    const PositionState& position = portfolio.position();
    const double market_value = position.market_value(market.close);
    const double equity = portfolio.cash() + market_value;
    result.equity_curve.push_back(equity);
    result.exposure.push_back(equity != 0.0 ? market_value / equity : 0.0);
    result.positions.push_back(PositionEvent{i, position.quantity, position.average_price,
                                             portfolio.cash(), market_value,
                                             position.unrealized_pnl(market.close),
                                             position.realized_pnl});

    if (i == n - 1) break;  // no bar left to execute on

    // --- 3. The target decided at this bar's close ---------------------------
    std::optional<OrderEvent> order = make_pending_order(
        i, i + 1, targets[i] - position.quantity, reasons.empty() ? kNoReason : reasons[i]);
    if (order.has_value()) {
      result.orders.push_back(*order);
      pending = std::move(order);
    }
  }

  // --- 4. Close out ---------------------------------------------------------
  if (config.liquidate_at_end) {
    liquidate(bars, portfolio, config, result);
    // The final equity point must reflect the liquidation, or a held winner
    // would be booked as though it had been cashed out for free. The final
    // *position* event is deliberately left as it was, matching engine.py: it
    // records the state the bar was marked at, not the close-out.
    result.equity_curve[n - 1] = portfolio.equity(bars[n - 1].close);
    result.exposure[n - 1] = 0.0;
  }

  result.trades = portfolio.trades();
  result.final_position = portfolio.position();
  result.final_cash = portfolio.cash();
  result.final_quantity = portfolio.position().quantity;
  result.realized_pnl = portfolio.realized_pnl();
  result.total_commission = portfolio.total_commission();
  result.total_slippage = portfolio.total_slippage();
  result.total_traded_notional = portfolio.total_traded_notional();
  return result;
}

}  // namespace quant
