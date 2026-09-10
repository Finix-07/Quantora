// The C++ mirror of services/quant/backtest/portfolio.py.
//
// Single-instrument by construction: the parity surface (see simulator.hpp) is
// one symbol's execution problem, so the multi-symbol map that portfolio.py
// carries for pair trading collapses to one PositionState here. The accounting
// rules are otherwise identical, case for case.

#ifndef QUANT_PORTFOLIO_HPP
#define QUANT_PORTFOLIO_HPP

#include <cstddef>
#include <string>
#include <vector>

#include "quant/events.hpp"

namespace quant {

//: One completed round trip, recorded when a position is reduced or closed.
//
// `pnl` is net of the costs attributable to both legs, because that is the
// number a user actually keeps; `gross_pnl` is kept alongside so the cost drag
// is visible rather than buried (NFR5.2).
struct Trade {
  int direction = 0;  //: +1 long, -1 short
  double quantity = 0.0;
  std::size_t entry_bar = 0;
  double entry_price = 0.0;
  std::size_t exit_bar = 0;
  double exit_price = 0.0;
  double gross_pnl = 0.0;
  double costs = 0.0;
  double pnl = 0.0;
  double return_pct = 0.0;
  long long bars_held = 0;
  std::string entry_reason;
  std::string exit_reason;
};

//: The open position.
struct PositionState {
  double quantity = 0.0;
  double average_price = 0.0;
  double realized_pnl = 0.0;
  std::size_t entry_bar_index = 0;
  std::string entry_reason;
  //: Entry-side costs still attached to the open position, released
  //: proportionally as the position is closed.
  double open_costs = 0.0;

  bool is_flat() const noexcept { return quantity == 0.0; }

  int sign() const noexcept {
    if (quantity > 0.0) return 1;
    if (quantity < 0.0) return -1;
    return 0;
  }

  double market_value(double price) const noexcept { return quantity * price; }

  double unrealized_pnl(double price) const noexcept {
    return (price - average_price) * quantity;
  }
};

class Portfolio {
 public:
  explicit Portfolio(double initial_cash);

  //: Update cash, position and trade history from one fill.
  //
  // Handles the three cases separately because they account differently:
  // opening or adding (average price moves), reducing (PnL is realized on the
  // closed quantity, average price does not move), and reversing (the old
  // position is fully closed and a new one opened at the fill price — blending
  // the two would produce an average price that never existed).
  void apply_fill(const FillEvent& fill, std::size_t bar_index);

  //: Whether a buy is affordable with cash on hand.
  //
  // Sells always pass: closing a long returns cash, and opening a short
  // receives proceeds. This models a cash account with short proceeds credited;
  // margin is deliberately not modelled, and that is stated in the result's
  // assumptions rather than left for the user to discover.
  bool can_afford(OrderSide side, double quantity, double fill_price,
                  double commission) const noexcept;

  //: Largest whole quantity buyable with current cash, including commission.
  double max_affordable_quantity(double fill_price, double commission_rate) const noexcept;

  double cash() const noexcept { return cash_; }
  double initial_cash() const noexcept { return initial_cash_; }
  const PositionState& position() const noexcept { return position_; }
  const std::vector<Trade>& trades() const noexcept { return trades_; }

  double equity(double price) const noexcept { return cash_ + position_.market_value(price); }
  double realized_pnl() const noexcept { return position_.realized_pnl; }

  double total_commission() const noexcept { return total_commission_; }
  double total_slippage() const noexcept { return total_slippage_; }
  //: Absolute traded notional, used for turnover.
  double total_traded_notional() const noexcept { return total_traded_notional_; }

 private:
  void open(const FillEvent& fill, double signed_quantity, double cost, std::size_t bar_index);
  void add(const FillEvent& fill, double signed_quantity, double cost);
  void close(const FillEvent& fill, double closing_quantity, double exit_cost,
             std::size_t bar_index);

  double initial_cash_;
  double cash_;
  PositionState position_;
  std::vector<Trade> trades_;
  double total_commission_ = 0.0;
  double total_slippage_ = 0.0;
  double total_traded_notional_ = 0.0;
};

}  // namespace quant

#endif  // QUANT_PORTFOLIO_HPP
