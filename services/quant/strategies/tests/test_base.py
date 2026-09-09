"""The strategy contract itself: parameter handling, sizing, exits, registry."""

from __future__ import annotations

import pandas as pd
import pytest

from services.quant.data.types import PriceSeries
from services.quant.strategies import (
    ExitContext,
    ExitDecision,
    InsufficientDataError,
    InvalidParametersError,
    ParameterSpec,
    Position,
    SignalDirection,
    SignalSet,
    SizingContext,
    Strategy,
    UnknownStrategyError,
    empty_signal_frame,
    registry,
)
from services.quant.testing import make_price_series


class DummyStrategy(Strategy):
    name = "dummy_for_tests"
    family = "test"
    description = "Always flat. Exists to exercise the contract."
    parameter_specs = (
        ParameterSpec("lookback", "int", 10, "Bars of history", minimum=2, maximum=100),
        ParameterSpec("threshold", "float", 0.5, "Trigger level", minimum=0.0, maximum=1.0),
        ParameterSpec("mode", "str", "a", "Which mode", choices=("a", "b")),
    )

    @property
    def warmup_bars(self) -> int:
        return int(self._parameters["lookback"])

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        self.require_warmup(series)
        return SignalSet(
            symbol=series.symbol,
            strategy=self.name,
            parameters=self.parameters,
            frame=empty_signal_frame(series.frame.index),
            warmup_bars=self.warmup_bars,
        )


def sizing_context(**overrides: object) -> SizingContext:
    base = {
        "timestamp": pd.Timestamp("2024-01-02", tz="Asia/Kolkata"),
        "symbol": "RELIANCE.NS",
        "direction": SignalDirection.LONG,
        "strength": 1.0,
        "price": 100.0,
        "equity": 100_000.0,
        "cash": 100_000.0,
        "position": Position(
            "RELIANCE.NS", 0.0, 0.0, pd.Timestamp("2024-01-01", tz="Asia/Kolkata"), 0
        ),
    }
    base.update(overrides)
    return SizingContext(**base)  # type: ignore[arg-type]


class TestParameters:
    def test_defaults_are_applied(self) -> None:
        assert DummyStrategy().parameters == {"lookback": 10, "threshold": 0.5, "mode": "a"}

    def test_overrides_are_applied_and_coerced(self) -> None:
        strategy = DummyStrategy(lookback=20, threshold=1)

        assert strategy.parameters["lookback"] == 20
        assert isinstance(strategy.parameters["threshold"], float)

    def test_unknown_parameter_is_rejected_not_ignored(self) -> None:
        # Silently dropping a parameter would run a different strategy than the
        # user configured, and the saved experiment would record the wrong thing.
        with pytest.raises(InvalidParametersError, match="Unknown parameter"):
            DummyStrategy(lookbak=20)

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"lookback": 1}, "must be >= 2"),
            ({"lookback": 500}, "must be <= 100"),
            ({"threshold": 2.0}, "must be <= 1.0"),
            ({"mode": "z"}, "must be one of"),
            ({"lookback": 3.5}, "must be a int"),
        ],
    )
    def test_out_of_range_values_are_rejected_specifically(
        self, kwargs: dict[str, object], expected: str
    ) -> None:
        with pytest.raises(InvalidParametersError, match=expected):
            DummyStrategy(**kwargs)  # type: ignore[arg-type]

    def test_parameters_are_a_copy(self) -> None:
        strategy = DummyStrategy()
        strategy.parameters["lookback"] = 999

        assert strategy.parameters["lookback"] == 10

    def test_describe_exposes_enough_to_build_a_form(self) -> None:
        described = DummyStrategy().describe()

        assert described["name"] == "dummy_for_tests"
        spec = next(s for s in described["parameter_specs"] if s["name"] == "lookback")
        assert spec["minimum"] == 2 and spec["maximum"] == 100
        assert spec["description"]


class TestWarmup:
    def test_short_series_raises_rather_than_signalling_on_partial_indicators(self) -> None:
        series = make_price_series([1.0] * 5)

        with pytest.raises(InsufficientDataError) as excinfo:
            DummyStrategy(lookback=10).generate_signals(series)

        # The message must tell the user how to fix it.
        assert "Widen the date range" in str(excinfo.value)
        assert excinfo.value.required == 10
        assert excinfo.value.available == 5

    def test_sufficient_series_produces_one_row_per_bar(self) -> None:
        series = make_price_series([float(i) for i in range(1, 21)])

        signals = DummyStrategy(lookback=10).generate_signals(series)

        assert len(signals) == 20


class TestDefaultSizing:
    def test_allocates_the_default_fraction_of_equity(self) -> None:
        quantity = DummyStrategy().position_size(sizing_context(equity=100_000.0, price=100.0))

        # 95% of 100_000 at 100/unit = 950 whole units.
        assert quantity == 950.0

    def test_short_direction_gives_a_negative_quantity(self) -> None:
        quantity = DummyStrategy().position_size(sizing_context(direction=SignalDirection.SHORT))

        assert quantity == -950.0

    def test_flat_direction_gives_zero(self) -> None:
        assert DummyStrategy().position_size(sizing_context(direction=SignalDirection.FLAT)) == 0.0

    def test_conviction_scales_the_position(self) -> None:
        half = DummyStrategy().position_size(sizing_context(strength=0.5))

        assert half == 475.0

    def test_quantities_are_whole_units(self) -> None:
        # These instruments are not fractionally tradable. Allowing fractions
        # would flatter every backtest by removing the rounding drag a real
        # trader pays.
        quantity = DummyStrategy().position_size(sizing_context(equity=1_000.0, price=333.0))

        assert quantity == float(int(quantity))
        assert quantity == 2.0

    def test_strength_is_clamped(self) -> None:
        assert DummyStrategy().position_size(sizing_context(strength=5.0)) == 950.0
        assert DummyStrategy().position_size(sizing_context(strength=-3.0)) == 0.0

    def test_non_positive_price_gives_zero_rather_than_dividing_by_zero(self) -> None:
        assert DummyStrategy().position_size(sizing_context(price=0.0)) == 0.0


class TestDefaultExit:
    def test_default_is_to_hold(self) -> None:
        context = ExitContext(
            timestamp=pd.Timestamp("2024-01-05", tz="Asia/Kolkata"),
            symbol="RELIANCE.NS",
            position=Position(
                "RELIANCE.NS", 10.0, 100.0, pd.Timestamp("2024-01-01", tz="Asia/Kolkata"), 3
            ),
            price=110.0,
            bar={"open": 105.0, "high": 112.0, "low": 104.0, "close": 110.0, "volume": 1.0},
            unrealized_pnl=100.0,
            unrealized_return=0.1,
        )

        assert DummyStrategy().exit_signal(context) == ExitDecision.hold()

    def test_exit_decision_carries_a_reason(self) -> None:
        # The reason is written into the trade record so the journal can analyse
        # performance by exit reason (FR8).
        decision = ExitDecision.exit("stop loss at -5%")

        assert decision.should_exit
        assert decision.reason == "stop loss at -5%"


class TestSignalSet:
    def test_rejects_a_frame_missing_required_columns(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="B", tz="Asia/Kolkata")

        with pytest.raises(ValueError, match="missing columns"):
            SignalSet(
                symbol="X",
                strategy="s",
                parameters={},
                frame=pd.DataFrame({"direction": [0, 0, 0]}, index=index),
            )

    def test_rejects_non_monotonic_timestamps(self) -> None:
        index = pd.DatetimeIndex(
            [
                pd.Timestamp("2024-01-03", tz="Asia/Kolkata"),
                pd.Timestamp("2024-01-01", tz="Asia/Kolkata"),
            ]
        )
        frame = empty_signal_frame(index)

        with pytest.raises(ValueError, match="non-monotonic"):
            SignalSet(symbol="X", strategy="s", parameters={}, frame=frame)

    def test_transitions_reports_only_changes(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="B", tz="Asia/Kolkata")
        frame = empty_signal_frame(index)
        frame.loc[index[1] :, "direction"] = int(SignalDirection.LONG)
        frame.loc[index[4], "direction"] = int(SignalDirection.FLAT)

        signals = SignalSet(symbol="X", strategy="s", parameters={}, frame=frame)

        # flat -> long at index 1, long -> flat at index 4, plus the first bar.
        assert len(signals.transitions()) == 3

    def test_direction_at_returns_the_enum(self) -> None:
        series = make_price_series([1.0] * 12)
        signals = DummyStrategy(lookback=2).generate_signals(series)

        assert signals.direction_at(series.frame.index[0]) is SignalDirection.FLAT


class TestRegistry:
    def test_register_and_create_round_trip(self) -> None:
        registry.register(DummyStrategy)
        try:
            strategy = registry.create("dummy_for_tests", {"lookback": 4})

            assert isinstance(strategy, DummyStrategy)
            assert strategy.parameters["lookback"] == 4
            assert "dummy_for_tests" in registry.available()
        finally:
            registry._REGISTRY.pop("dummy_for_tests", None)

    def test_unknown_strategy_lists_what_is_available(self) -> None:
        with pytest.raises(UnknownStrategyError, match="Available:"):
            registry.create("no_such_strategy")

    def test_duplicate_name_is_rejected(self) -> None:
        registry.register(DummyStrategy)
        try:

            class Clashing(Strategy):
                name = "dummy_for_tests"

                def generate_signals(self, series: PriceSeries) -> SignalSet:  # pragma: no cover
                    raise NotImplementedError

            # Names identify saved experiments, so a collision must be loud.
            with pytest.raises(ValueError, match="already registered"):
                registry.register(Clashing)
        finally:
            registry._REGISTRY.pop("dummy_for_tests", None)

    def test_registering_a_nameless_strategy_is_rejected(self) -> None:
        class Nameless(Strategy):
            def generate_signals(self, series: PriceSeries) -> SignalSet:  # pragma: no cover
                raise NotImplementedError

        with pytest.raises(ValueError, match="non-empty `name`"):
            registry.register(Nameless)


def test_strategy_is_independent_of_transport_layers() -> None:
    """The invariant from architecture.md §5, asserted rather than assumed."""
    import pathlib

    import services.quant.strategies.base as base_module

    source = base_module.__file__
    assert source is not None
    text = pathlib.Path(source).read_text(encoding="utf-8")

    for forbidden in ("import fastapi", "import httpx", "import psycopg", "import requests"):
        assert forbidden not in text, f"strategy contract must not depend on {forbidden}"


def test_empty_signal_frame_has_the_contract_dtypes() -> None:
    index = pd.date_range("2024-01-01", periods=3, freq="B", tz="Asia/Kolkata")

    frame = empty_signal_frame(index)

    assert list(frame.columns) == ["direction", "strength", "reason"]
    assert frame["direction"].dtype == "int64"
    assert frame["strength"].dtype == "float64"
    assert (frame["direction"] == int(SignalDirection.FLAT)).all()


class TestStringParameters:
    """`str` specs were unchecked until a strategy needed one (pair_symbol).

    An unvalidated string parameter is not harmless here: `pair_symbol` names
    the instrument whose bars the strategy will be given, and it is persisted
    with the experiment. Accepting a non-string would record a configuration
    that could never be reproduced.
    """

    def test_non_string_is_rejected_rather_than_coerced(self) -> None:
        # str(5) would be a plausible-looking "5" that names no instrument.
        # Uses a choice-free spec, because a spec WITH choices rejects the value
        # on the choices check first and would not exercise the type branch.
        spec = ParameterSpec("pair_symbol", "str", "TCS.NS", "The second leg")

        for bad in (5, 5.0, ["TCS.NS"], None):
            with pytest.raises(InvalidParametersError, match="must be a non-empty str"):
                spec.validate(bad)

    def test_empty_and_whitespace_only_values_are_rejected(self) -> None:
        spec = ParameterSpec("pair_symbol", "str", "TCS.NS", "The second leg")

        for bad in ("", "   "):
            with pytest.raises(InvalidParametersError, match="must be a non-empty str"):
                spec.validate(bad)

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        spec = ParameterSpec("pair_symbol", "str", "TCS.NS", "The second leg")

        assert spec.validate("  TCS.NS  ") == "TCS.NS"

    def test_choices_are_still_enforced(self) -> None:
        with pytest.raises(InvalidParametersError, match="must be one of"):
            DummyStrategy(mode="z")

    def test_numeric_bounds_are_not_applied_to_strings(self) -> None:
        # Comparing a str against a number raises TypeError with no useful
        # message; a spec that carries bounds it cannot apply must not crash.
        spec = ParameterSpec("label", "str", "abc", "A label", minimum=0, maximum=10)

        assert spec.validate("abc") == "abc"
