// The thin Python binding for the C++ executor.
//
// architecture.md §7 permits this layer only once the C++ API is stable and a
// benchmark exists, so it deliberately adds no behaviour of its own: it moves
// numbers across the boundary and nothing else. In particular it does not
// format warning text (engine.py owns the wording), does not touch timestamps
// (bar indices cross the boundary and the wrapper maps them back onto the
// pandas index), and does not fall back to anything if it is missing — an
// absent extension must be a loud error, not a silent Python run wearing a C++
// label, or the parity test and the benchmark would both be measuring nothing.
//
// Bars arrive as five contiguous float64 arrays rather than as a list of Python
// objects: the point of this path is throughput, and rebuilding a million
// PyObjects would dominate everything the engine does.

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

#include "quant/simulator.hpp"

namespace py = pybind11;

namespace {

using Array = py::array_t<double, py::array::c_style | py::array::forcecast>;

const double* checked(const Array& array, const char* name, std::size_t expected) {
  if (array.ndim() != 1) {
    throw std::invalid_argument(std::string(name) + " must be a one-dimensional array");
  }
  if (static_cast<std::size_t>(array.shape(0)) != expected) {
    throw std::invalid_argument(std::string(name) + " must have one entry per bar");
  }
  return array.data();
}

std::vector<quant::Bar> to_bars(const Array& open, const Array& high, const Array& low,
                                const Array& close, const Array& volume) {
  if (open.ndim() != 1) throw std::invalid_argument("open must be a one-dimensional array");
  const auto n = static_cast<std::size_t>(open.shape(0));
  const double* o = open.data();
  const double* h = checked(high, "high", n);
  const double* l = checked(low, "low", n);
  const double* c = checked(close, "close", n);
  const double* v = checked(volume, "volume", n);

  std::vector<quant::Bar> bars(n);
  for (std::size_t i = 0; i < n; ++i) {
    bars[i] = quant::Bar{o[i], h[i], l[i], c[i], v[i]};
  }
  return bars;
}

py::array_t<double> to_array(const std::vector<double>& values) {
  py::array_t<double> out(static_cast<py::ssize_t>(values.size()));
  std::copy(values.begin(), values.end(), out.mutable_data());
  return out;
}

}  // namespace

PYBIND11_MODULE(quant_execution, m) {
  m.doc() = "C++ execution engine for Quantora backtests (services/execution-cpp).";

  py::enum_<quant::OrderSide>(m, "OrderSide")
      .value("BUY", quant::OrderSide::Buy)
      .value("SELL", quant::OrderSide::Sell);

  py::enum_<quant::PriceField>(m, "PriceField")
      .value("OPEN", quant::PriceField::Open)
      .value("CLOSE", quant::PriceField::Close);

  py::enum_<quant::ExecutionWarning::Kind>(m, "WarningKind")
      .value("SKIPPED_BUY", quant::ExecutionWarning::Kind::SkippedBuy)
      .value("REDUCED_BUY", quant::ExecutionWarning::Kind::ReducedBuy);

  py::class_<quant::CostModel>(m, "CostModel")
      .def(py::init([](double commission_bps, double commission_min, double slippage_bps,
                       double spread_bps) {
             return quant::CostModel{commission_bps, commission_min, slippage_bps, spread_bps};
           }),
           py::arg("commission_bps") = 3.0, py::arg("commission_min") = 0.0,
           py::arg("slippage_bps") = 5.0, py::arg("spread_bps") = 2.0)
      .def_readwrite("commission_bps", &quant::CostModel::commission_bps)
      .def_readwrite("commission_min", &quant::CostModel::commission_min)
      .def_readwrite("slippage_bps", &quant::CostModel::slippage_bps)
      .def_readwrite("spread_bps", &quant::CostModel::spread_bps);

  py::class_<quant::SimulationConfig>(m, "SimulationConfig")
      .def(py::init([](double initial_cash, const quant::CostModel& costs,
                       quant::PriceField price_field, bool liquidate_at_end) {
             quant::SimulationConfig config;
             config.initial_cash = initial_cash;
             config.costs = costs;
             config.price_field = price_field;
             config.liquidate_at_end = liquidate_at_end;
             return config;
           }),
           py::arg("initial_cash") = 1000000.0, py::arg("costs") = quant::CostModel{},
           py::arg("price_field") = quant::PriceField::Open,
           py::arg("liquidate_at_end") = true)
      .def_readwrite("initial_cash", &quant::SimulationConfig::initial_cash)
      .def_readwrite("costs", &quant::SimulationConfig::costs)
      .def_readwrite("price_field", &quant::SimulationConfig::price_field)
      .def_readwrite("liquidate_at_end", &quant::SimulationConfig::liquidate_at_end);

  py::class_<quant::OrderEvent>(m, "OrderEvent")
      .def_readonly("decision_bar", &quant::OrderEvent::decision_bar)
      .def_readonly("bar_index", &quant::OrderEvent::bar_index)
      .def_readonly("side", &quant::OrderEvent::side)
      .def_readonly("quantity", &quant::OrderEvent::quantity)
      .def_readonly("reason", &quant::OrderEvent::reason);

  py::class_<quant::FillEvent>(m, "FillEvent")
      .def_readonly("bar_index", &quant::FillEvent::bar_index)
      .def_readonly("side", &quant::FillEvent::side)
      .def_readonly("quantity", &quant::FillEvent::quantity)
      .def_readonly("reference_price", &quant::FillEvent::reference_price)
      .def_readonly("fill_price", &quant::FillEvent::fill_price)
      .def_readonly("commission", &quant::FillEvent::commission)
      .def_readonly("reason", &quant::FillEvent::reason)
      .def_property_readonly("slippage_cost", &quant::FillEvent::slippage_cost);

  py::class_<quant::PositionEvent>(m, "PositionEvent")
      .def_readonly("bar_index", &quant::PositionEvent::bar_index)
      .def_readonly("quantity", &quant::PositionEvent::quantity)
      .def_readonly("average_price", &quant::PositionEvent::average_price)
      .def_readonly("cash", &quant::PositionEvent::cash)
      .def_readonly("market_value", &quant::PositionEvent::market_value)
      .def_readonly("unrealized_pnl", &quant::PositionEvent::unrealized_pnl)
      .def_readonly("realized_pnl", &quant::PositionEvent::realized_pnl);

  py::class_<quant::PositionState>(m, "PositionState")
      .def_readonly("quantity", &quant::PositionState::quantity)
      .def_readonly("average_price", &quant::PositionState::average_price)
      .def_readonly("realized_pnl", &quant::PositionState::realized_pnl)
      .def_readonly("entry_bar_index", &quant::PositionState::entry_bar_index)
      .def_readonly("entry_reason", &quant::PositionState::entry_reason)
      .def_readonly("open_costs", &quant::PositionState::open_costs);

  py::class_<quant::Trade>(m, "Trade")
      .def_readonly("direction", &quant::Trade::direction)
      .def_readonly("quantity", &quant::Trade::quantity)
      .def_readonly("entry_bar", &quant::Trade::entry_bar)
      .def_readonly("entry_price", &quant::Trade::entry_price)
      .def_readonly("exit_bar", &quant::Trade::exit_bar)
      .def_readonly("exit_price", &quant::Trade::exit_price)
      .def_readonly("gross_pnl", &quant::Trade::gross_pnl)
      .def_readonly("costs", &quant::Trade::costs)
      .def_readonly("pnl", &quant::Trade::pnl)
      .def_readonly("return_pct", &quant::Trade::return_pct)
      .def_readonly("bars_held", &quant::Trade::bars_held)
      .def_readonly("entry_reason", &quant::Trade::entry_reason)
      .def_readonly("exit_reason", &quant::Trade::exit_reason);

  py::class_<quant::ExecutionWarning>(m, "ExecutionWarning")
      .def_readonly("bar_index", &quant::ExecutionWarning::bar_index)
      .def_readonly("kind", &quant::ExecutionWarning::kind)
      .def_readonly("requested_quantity", &quant::ExecutionWarning::requested_quantity)
      .def_readonly("filled_quantity", &quant::ExecutionWarning::filled_quantity)
      .def_readonly("fill_price", &quant::ExecutionWarning::fill_price);

  py::class_<quant::SimulationResult>(m, "SimulationResult")
      .def_property_readonly(
          "equity_curve",
          [](const quant::SimulationResult& r) { return to_array(r.equity_curve); })
      .def_property_readonly(
          "exposure", [](const quant::SimulationResult& r) { return to_array(r.exposure); })
      .def_readonly("orders", &quant::SimulationResult::orders)
      .def_readonly("fills", &quant::SimulationResult::fills)
      .def_readonly("positions", &quant::SimulationResult::positions)
      .def_readonly("trades", &quant::SimulationResult::trades)
      .def_readonly("warnings", &quant::SimulationResult::warnings)
      .def_readonly("final_position", &quant::SimulationResult::final_position)
      .def_readonly("final_cash", &quant::SimulationResult::final_cash)
      .def_readonly("final_quantity", &quant::SimulationResult::final_quantity)
      .def_readonly("realized_pnl", &quant::SimulationResult::realized_pnl)
      .def_readonly("total_commission", &quant::SimulationResult::total_commission)
      .def_readonly("total_slippage", &quant::SimulationResult::total_slippage)
      .def_readonly("total_traded_notional", &quant::SimulationResult::total_traded_notional);

  m.def(
      "simulate_targets",
      [](const Array& open, const Array& high, const Array& low, const Array& close,
         const Array& volume, const Array& targets, const std::vector<std::string>& reasons,
         const quant::SimulationConfig& config) {
        const std::vector<quant::Bar> bars = to_bars(open, high, low, close, volume);
        const double* target_data = checked(targets, "targets", bars.size());
        const std::vector<double> target_vector(target_data, target_data + bars.size());
        // The GIL is not needed once the inputs are copied out of numpy, and
        // holding it through a million-bar run would block the interpreter for
        // the whole simulation.
        py::gil_scoped_release release;
        return quant::simulate_targets(bars, target_vector, reasons, config);
      },
      py::arg("open"), py::arg("high"), py::arg("low"), py::arg("close"), py::arg("volume"),
      py::arg("targets"), py::arg("reasons"), py::arg("config"),
      "Execute a pre-computed per-bar target position. See simulator.hpp.");
}
