// The C++ mirror of services/quant/backtest/events.py.
//
// architecture.md §7 names four events — MarketEvent, OrderEvent, FillEvent,
// PositionEvent — and the Python module of the same name already fixes their
// shape. These are the same four, kept plain and copyable: no virtual dispatch,
// no shared_ptr, no base class. An event here is a value that gets pushed into a
// vector a few million times, and a vtable pointer per event buys nothing when
// there is exactly one kind of each.
//
// Two deliberate differences from events.py, both because the executor is
// single-instrument by construction (see simulator.hpp):
//
//  * No `symbol` field. One run covers one instrument, so a string copy on every
//    event would be pure overhead; the binding attaches the symbol on the way
//    out.
//  * Bars are identified by `bar_index`, not by a timestamp. Every event in a
//    simulation happens *at a bar*, and an index is exact, cheap and free of
//    timezone semantics — the binding maps it back to the pandas index, so the
//    calendar never has to be reimplemented here.

#ifndef QUANT_EVENTS_HPP
#define QUANT_EVENTS_HPP

#include <cstddef>
#include <string>

namespace quant {

// The enumerator values are the sign convention used throughout the arithmetic
// (+1 buy, -1 sell), matching OrderSide.sign in events.py, so `side_sign` is a
// cast rather than a branch.
enum class OrderSide : int {
  Buy = 1,
  Sell = -1,
};

constexpr int side_sign(OrderSide side) noexcept { return static_cast<int>(side); }

//: One bar of market data becoming available.
struct MarketEvent {
  std::size_t bar_index = 0;
  double open = 0.0;
  double high = 0.0;
  double low = 0.0;
  double close = 0.0;
  double volume = 0.0;
};

//: An intent to trade, created at the close of `decision_bar`.
//
// Two bar indices, deliberately, exactly as OrderEvent carries two timestamps in
// Python: `decision_bar` is the bar whose data justified the order, `bar_index`
// is the bar it is eligible to execute on. Keeping them separate makes a
// look-ahead violation a field comparison rather than something to reason about.
struct OrderEvent {
  std::size_t decision_bar = 0;
  std::size_t bar_index = 0;
  OrderSide side = OrderSide::Buy;
  double quantity = 0.0;
  std::string reason;
};

//: An order that executed, with the costs it actually incurred.
struct FillEvent {
  std::size_t bar_index = 0;
  OrderSide side = OrderSide::Buy;
  double quantity = 0.0;
  //: The bar price the fill was benchmarked against, before costs.
  double reference_price = 0.0;
  //: What was actually paid or received, after slippage and half-spread.
  double fill_price = 0.0;
  double commission = 0.0;
  std::string reason;

  double notional() const noexcept { return quantity * fill_price; }

  //: Cost of the gap between the reference price and the fill price.
  double slippage_cost() const noexcept {
    const double gap = fill_price - reference_price;
    return (gap < 0.0 ? -gap : gap) * quantity;
  }

  double total_cost() const noexcept { return commission + slippage_cost(); }

  //: Buying spends, selling receives; commission always costs.
  double cash_delta() const noexcept {
    return -static_cast<double>(side_sign(side)) * notional() - commission;
  }
};

//: Portfolio state after a bar has been processed.
struct PositionEvent {
  std::size_t bar_index = 0;
  double quantity = 0.0;
  double average_price = 0.0;
  double cash = 0.0;
  double market_value = 0.0;
  double unrealized_pnl = 0.0;
  double realized_pnl = 0.0;

  double equity() const noexcept { return cash + market_value; }
};

}  // namespace quant

#endif  // QUANT_EVENTS_HPP
