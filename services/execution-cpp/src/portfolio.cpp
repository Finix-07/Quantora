#include "quant/portfolio.hpp"

#include <cmath>
#include <stdexcept>

namespace quant {

Portfolio::Portfolio(double initial_cash) : initial_cash_(initial_cash), cash_(initial_cash) {
  if (!(initial_cash > 0.0)) {
    // Same rule as portfolio.py: a backtest that quietly runs on imaginary money
    // reports returns the user could never have earned (NFR5.6).
    throw std::invalid_argument("initial_cash must be positive");
  }
}

void Portfolio::apply_fill(const FillEvent& fill, std::size_t bar_index) {
  const double signed_quantity = static_cast<double>(side_sign(fill.side)) * fill.quantity;

  cash_ += fill.cash_delta();
  total_commission_ += fill.commission;
  total_slippage_ += fill.slippage_cost();
  const double notional = fill.notional();
  total_traded_notional_ += notional < 0.0 ? -notional : notional;
  const double cost = fill.total_cost();

  if (position_.is_flat()) {
    open(fill, signed_quantity, cost, bar_index);
    return;
  }

  if (position_.sign() == side_sign(fill.side)) {
    add(fill, signed_quantity, cost);
    return;
  }

  const double incoming = signed_quantity < 0.0 ? -signed_quantity : signed_quantity;
  const double held = position_.quantity < 0.0 ? -position_.quantity : position_.quantity;
  const double closing_quantity = incoming < held ? incoming : held;
  close(fill, closing_quantity, cost, bar_index);

  const double remaining = incoming - closing_quantity;
  if (remaining > 0.0) {
    // Reversal: the rest opens a new position on the other side. The entry-side
    // cost of that new position is the share of this fill's cost proportional to
    // the reversing quantity.
    const double opening_cost = cost * (remaining / incoming);
    FillEvent reversal = fill;
    reversal.quantity = remaining;
    reversal.commission = 0.0;
    open(reversal, static_cast<double>(side_sign(fill.side)) * remaining, opening_cost, bar_index);
  }
}

void Portfolio::open(const FillEvent& fill, double signed_quantity, double cost,
                     std::size_t bar_index) {
  position_.quantity = signed_quantity;
  position_.average_price = fill.fill_price;
  position_.entry_bar_index = bar_index;
  position_.entry_reason = fill.reason;
  position_.open_costs = cost;
}

void Portfolio::add(const FillEvent& fill, double signed_quantity, double cost) {
  const double new_quantity = position_.quantity + signed_quantity;
  position_.average_price =
      (position_.average_price * position_.quantity + fill.fill_price * signed_quantity) /
      new_quantity;
  position_.quantity = new_quantity;
  position_.open_costs += cost;
}

void Portfolio::close(const FillEvent& fill, double closing_quantity, double exit_cost,
                      std::size_t bar_index) {
  const int direction_sign = position_.sign();
  const double held = position_.quantity < 0.0 ? -position_.quantity : position_.quantity;
  const double fraction = closing_quantity / held;
  const double entry_cost_share = position_.open_costs * fraction;
  const double exit_cost_share =
      fill.quantity != 0.0 ? exit_cost * (closing_quantity / fill.quantity) : 0.0;

  const double gross = (fill.fill_price - position_.average_price) * closing_quantity *
                       static_cast<double>(direction_sign);
  const double costs = entry_cost_share + exit_cost_share;
  const double net = gross - costs;

  position_.realized_pnl += net;
  position_.open_costs -= entry_cost_share;

  const double entry_notional = position_.average_price * closing_quantity;

  Trade trade;
  trade.direction = direction_sign;
  trade.quantity = closing_quantity;
  trade.entry_bar = position_.entry_bar_index;
  trade.entry_price = position_.average_price;
  trade.exit_bar = fill.bar_index;
  trade.exit_price = fill.fill_price;
  trade.gross_pnl = gross;
  trade.costs = costs;
  trade.pnl = net;
  trade.return_pct = entry_notional != 0.0 ? net / entry_notional : 0.0;
  // Signed subtraction, then clamped: bar_index is always at or after the entry
  // bar in a forward simulation, but an unsigned wrap here would turn a
  // programming error into a nonsensical holding period rather than a zero.
  const long long span = static_cast<long long>(bar_index) -
                         static_cast<long long>(position_.entry_bar_index);
  trade.bars_held = span > 0 ? span : 0;
  trade.entry_reason = position_.entry_reason;
  trade.exit_reason = fill.reason;
  trades_.push_back(std::move(trade));

  position_.quantity += static_cast<double>(direction_sign) * -closing_quantity;
  if (std::fabs(position_.quantity) < 1e-12) {
    position_.quantity = 0.0;
    position_.average_price = 0.0;
    position_.entry_reason.clear();
    position_.open_costs = 0.0;
  }
}

bool Portfolio::can_afford(OrderSide side, double quantity, double fill_price,
                           double commission) const noexcept {
  if (side == OrderSide::Sell) return true;
  return cash_ >= quantity * fill_price + commission;
}

double Portfolio::max_affordable_quantity(double fill_price,
                                          double commission_rate) const noexcept {
  if (fill_price <= 0.0) return 0.0;
  const double per_unit = fill_price * (1.0 + commission_rate);
  if (!(per_unit > 0.0)) return 0.0;
  // `std::trunc`, not `floor`: Python's `int()` truncates toward zero, and the
  // sign matters — a negative cash balance must yield 0 or less (i.e. "skip"),
  // never a rounded-down negative that then reads as a tradable size.
  return std::trunc(cash_ / per_unit);
}

}  // namespace quant
