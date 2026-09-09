"""CostModel: fill-price slippage/spread penalty, commission, and (de)serialization.

Every number here is computed independently in the test (never by calling the
model under test) so a bug in the implementation cannot also corrupt the
expectation.
"""

from __future__ import annotations

import pytest

from services.quant.backtest.costs import CostModel


class TestFillPrice:
    def test_buying_moves_price_up_by_exactly_slippage_plus_half_spread(self) -> None:
        model = CostModel(commission_bps=3.0, commission_min=0.0, slippage_bps=5.0, spread_bps=2.0)

        # penalty = (5 + 2/2) / 10000 = 6 / 10000 = 0.0006
        # buy fill = 100 * (1 + 0.0006) = 100.06
        price = model.fill_price(100.0, side=1)

        assert price == pytest.approx(100.06)

    def test_selling_moves_price_down_by_exactly_slippage_plus_half_spread(self) -> None:
        model = CostModel(commission_bps=3.0, commission_min=0.0, slippage_bps=5.0, spread_bps=2.0)

        # penalty = 0.0006 as above; sell fill = 100 * (1 - 0.0006) = 99.94
        price = model.fill_price(100.0, side=-1)

        assert price == pytest.approx(99.94)

    def test_round_trip_penalises_both_sides(self) -> None:
        # Buying at 100 then immediately selling back at 100 (same reference
        # price) must cost roughly twice the one-way penalty, not zero and not
        # a single side's worth of drag.
        model = CostModel(commission_bps=0.0, commission_min=0.0, slippage_bps=5.0, spread_bps=2.0)
        one_way_penalty = (5.0 + 2.0 / 2.0) / 10_000.0  # 0.0006

        buy = model.fill_price(100.0, side=1)  # 100.06
        sell = model.fill_price(100.0, side=-1)  # 99.94
        round_trip_cost = buy - sell  # 0.12

        assert round_trip_cost == pytest.approx(100.0 * one_way_penalty * 2)
        assert round_trip_cost == pytest.approx(0.12)

    def test_invalid_side_raises(self) -> None:
        model = CostModel()

        with pytest.raises(ValueError, match=r"side must be \+1"):
            model.fill_price(100.0, side=0)

        with pytest.raises(ValueError, match=r"side must be \+1"):
            model.fill_price(100.0, side=2)


class TestCommission:
    def test_commission_is_notional_times_bps(self) -> None:
        model = CostModel(commission_bps=3.0, commission_min=0.0)

        # 10_000 * 3 / 10_000 = 3.0
        assert model.commission(10_000.0) == pytest.approx(3.0)

    def test_commission_min_acts_as_a_floor(self) -> None:
        model = CostModel(commission_bps=3.0, commission_min=25.0)

        # notional so small the bps rate would give 0.3, but the floor is 25.
        assert model.commission(1_000.0) == pytest.approx(25.0)

    def test_commission_uses_absolute_notional(self) -> None:
        # A sell has negative notional in this codebase's convention; commission
        # must never come out negative because of that sign.
        model = CostModel(commission_bps=3.0, commission_min=0.0)

        assert model.commission(-10_000.0) == pytest.approx(3.0)
        assert model.commission(-10_000.0) >= 0.0


class TestConstruction:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"commission_bps": -1.0},
            {"commission_min": -1.0},
            {"slippage_bps": -1.0},
            {"spread_bps": -1.0},
        ],
    )
    def test_negative_parameters_are_rejected(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            CostModel(**kwargs)


class TestZero:
    def test_zero_moves_no_price(self) -> None:
        model = CostModel.zero()

        assert model.fill_price(100.0, side=1) == pytest.approx(100.0)
        assert model.fill_price(100.0, side=-1) == pytest.approx(100.0)

    def test_zero_charges_no_commission(self) -> None:
        model = CostModel.zero()

        assert model.commission(10_000.0) == 0.0


class TestSerialization:
    def test_as_dict_from_dict_round_trip(self) -> None:
        model = CostModel(commission_bps=4.5, commission_min=10.0, slippage_bps=6.5, spread_bps=1.5)

        restored = CostModel.from_dict(model.as_dict())

        assert restored == model
        assert restored.as_dict() == model.as_dict()

    def test_from_dict_none_gives_defaults(self) -> None:
        assert CostModel.from_dict(None) == CostModel()

    def test_from_dict_empty_gives_defaults(self) -> None:
        assert CostModel.from_dict({}) == CostModel()

    def test_from_dict_rejects_unknown_key(self) -> None:
        # Silently dropping an unrecognised cost parameter on a rerun would
        # produce different numbers while claiming the run was reproduced.
        with pytest.raises(ValueError, match="Unknown cost-model field"):
            CostModel.from_dict({"commission_bps": 3.0, "exchange_fee_bps": 1.0})


class TestDescribe:
    def test_normal_model_mentions_commission_slippage_and_spread(self) -> None:
        description = CostModel(commission_bps=3.0, slippage_bps=5.0, spread_bps=2.0).describe()

        assert "commission" in description
        assert "slippage" in description
        assert "spread" in description

    def test_zero_model_says_it_is_frictionless_and_optimistic(self) -> None:
        description = CostModel.zero().describe()

        assert "frictionless" in description or "no transaction costs" in description.lower()
        assert "optimistic" in description
