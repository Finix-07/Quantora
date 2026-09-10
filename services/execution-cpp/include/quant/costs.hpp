// The C++ mirror of services/quant/backtest/costs.py.
//
// The arithmetic here is written to match the Python expressions *term for
// term*, not merely to be algebraically equivalent. Floating-point addition is
// not associative, so `(slippage + spread/2) / 10000` and
// `slippage/10000 + spread/20000` can differ in the last bit — and the parity
// test compares the two engines at 1e-12. Any "simplification" of these
// expressions is a behaviour change.

#ifndef QUANT_COSTS_HPP
#define QUANT_COSTS_HPP

#include <stdexcept>

namespace quant {

inline constexpr double kBps = 10000.0;

struct CostModel {
  //: Broker commission per side, in basis points of traded notional.
  double commission_bps = 3.0;
  //: Minimum commission per fill, in account currency.
  double commission_min = 0.0;
  //: Adverse price movement between the decision and the fill, per side.
  double slippage_bps = 5.0;
  //: Full bid-ask spread. Half is paid on each side.
  double spread_bps = 2.0;

  //: The price actually paid (side = +1) or received (side = -1).
  double fill_price(double reference_price, int side) const {
    if (side != 1 && side != -1) {
      throw std::invalid_argument("side must be +1 (buy) or -1 (sell)");
    }
    const double penalty = (slippage_bps + spread_bps / 2.0) / kBps;
    return reference_price * (1.0 + static_cast<double>(side) * penalty);
  }

  //: Commission for one fill of the given absolute notional.
  double commission(double notional) const noexcept {
    const double magnitude = notional < 0.0 ? -notional : notional;
    const double proportional = magnitude * commission_bps / kBps;
    return commission_min > proportional ? commission_min : proportional;
  }

  //: A frictionless model. Never a default — see costs.py for why.
  static CostModel zero() noexcept { return CostModel{0.0, 0.0, 0.0, 0.0}; }
};

}  // namespace quant

#endif  // QUANT_COSTS_HPP
