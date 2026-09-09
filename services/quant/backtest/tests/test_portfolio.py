"""Portfolio accounting: cash, positions, realized PnL and the trade ledger.

FillEvents are built directly with simple round numbers so every expected
value in these tests is hand-computable and the arithmetic is written out in a
comment next to the assertion.
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.quant.backtest.events import FillEvent, OrderSide
from services.quant.backtest.portfolio import Portfolio


def make_fill(
    *,
    symbol: str = "X",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 10.0,
    # Defaults to fill_price so a test that says "no slippage" gets none. Setting
    # fill_price alone while reference_price stayed pinned at 100 would silently
    # inject |fill - 100| * quantity of slippage cost into the expected numbers.
    reference_price: float | None = None,
    fill_price: float = 100.0,
    commission: float = 0.0,
    timestamp: pd.Timestamp | None = None,
    reason: str = "",
) -> FillEvent:
    return FillEvent(
        timestamp=timestamp
        if timestamp is not None
        else pd.Timestamp("2024-01-01", tz="Asia/Kolkata"),
        symbol=symbol,
        side=side,
        quantity=quantity,
        reference_price=fill_price if reference_price is None else reference_price,
        fill_price=fill_price,
        commission=commission,
        reason=reason,
    )


class TestOpeningAndAdding:
    def test_opening_a_long(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)

        # cash_delta = -(1 * 10 * 100) - 5 = -1005
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )

        assert portfolio.cash == pytest.approx(100_000.0 - 1_005.0)
        position = portfolio.position("X")
        assert position.quantity == 10.0
        assert position.average_price == pytest.approx(100.0)

    def test_adding_to_a_long_blends_average_price(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=5.0, fill_price=110.0, commission=2.0),
            bar_index=1,
        )

        position = portfolio.position("X")
        # weighted average = (100*10 + 110*5) / 15 = 1550 / 15 = 103.3333...
        assert position.quantity == 15.0
        assert position.average_price == pytest.approx(1_550.0 / 15.0)

        # cash: 100_000 - 1005 (first fill) - (5*110 + 2) (second fill)
        assert portfolio.cash == pytest.approx(100_000.0 - 1_005.0 - 552.0)


class TestReducingAndClosing:
    def _opened_long(self) -> Portfolio:
        portfolio = Portfolio(initial_cash=100_000.0)
        # commission 5, no slippage (reference == fill) -> open_costs = 5
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )
        return portfolio

    def test_reducing_a_long_realizes_pnl_net_of_attributable_costs_and_keeps_average_price(
        self,
    ) -> None:
        portfolio = self._opened_long()

        # Sell 4 of 10 at 110, commission 3, no slippage.
        # fraction closed = 4/10 = 0.4; entry_cost_share = 5 * 0.4 = 2.0
        # exit_cost = commission(3) + slippage(0) = 3; exit_cost_share = 3 * (4/4) = 3.0
        # gross = (110 - 100) * 4 * 1 = 40; costs = 2 + 3 = 5; net = 35
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=4.0, fill_price=110.0, commission=3.0),
            bar_index=1,
        )

        position = portfolio.position("X")
        assert position.quantity == 6.0
        assert position.average_price == pytest.approx(100.0)  # unchanged by a reduction

        assert len(portfolio.trades) == 1
        trade = portfolio.trades[0]
        assert trade.gross_pnl == pytest.approx(40.0)
        assert trade.costs == pytest.approx(5.0)
        assert trade.pnl == pytest.approx(35.0)

    def test_fully_closing_returns_to_exactly_flat(self) -> None:
        portfolio = self._opened_long()

        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=10.0, fill_price=90.0, commission=1.0),
            bar_index=1,
        )

        position = portfolio.position("X")
        assert position.quantity == 0.0
        assert position.average_price == 0.0
        assert len(portfolio.trades) == 1


class TestReversal:
    def test_long_to_short_in_one_fill_closes_old_and_opens_new_at_fill_price(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=0.0),
            bar_index=0,
        )

        # Sell 15: 10 close the long, 5 open a new short.
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=15.0, fill_price=90.0, commission=0.0),
            bar_index=1,
        )

        # Exactly one Trade recorded for the closed long.
        assert len(portfolio.trades) == 1
        closing_trade = portfolio.trades[0]
        assert closing_trade.direction == "long"
        assert closing_trade.quantity == 10.0
        # gross = (90 - 100) * 10 * 1 = -100, no costs.
        assert closing_trade.pnl == pytest.approx(-100.0)

        position = portfolio.position("X")
        assert position.quantity == -5.0
        # The new average price is the fill price itself, not a blend of 100
        # (old average) and 90 (fill price) -- that blended number never
        # existed as a position anyone held.
        assert position.average_price == pytest.approx(90.0)


class TestShorting:
    def test_opening_a_short_credits_cash_and_covering_lower_realizes_profit(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)

        # Open short: cash_delta = -(-1 * 10 * 100) - 5 = 995
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )
        assert portfolio.cash == pytest.approx(100_000.0 + 995.0)
        position = portfolio.position("X")
        assert position.quantity == -10.0
        assert position.average_price == pytest.approx(100.0)

        # Cover at 80, commission 3, no slippage.
        # entry_cost_share = 5 * 1.0 = 5; exit_cost_share = 3 * (10/10) = 3
        # gross = (80 - 100) * 10 * (-1) = 200; costs = 8; net = 192
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=80.0, commission=3.0),
            bar_index=1,
        )

        trade = portfolio.trades[0]
        assert trade.direction == "short"
        assert trade.gross_pnl == pytest.approx(200.0)
        assert trade.costs == pytest.approx(8.0)
        assert trade.pnl == pytest.approx(192.0)

        position = portfolio.position("X")
        assert position.quantity == 0.0


class TestTradeAccounting:
    def test_pnl_is_net_of_costs_gross_is_not(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=80.0, commission=3.0),
            bar_index=1,
        )

        trade = portfolio.trades[0]
        assert trade.gross_pnl - trade.costs == pytest.approx(trade.pnl)

    def test_is_win_reflects_net_pnl_not_gross(self) -> None:
        # Open long with zero cost, then close it at a small profit whose
        # commission exceeds the gross gain: gross > 0 but net < 0. A trade
        # that only looks profitable before costs must not count as a win.
        portfolio = Portfolio(initial_cash=100_000.0)
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=0.0),
            bar_index=0,
        )
        # gross = (101 - 100) * 10 * 1 = 10; costs = 0 + 15 = 15; net = -5
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=10.0, fill_price=101.0, commission=15.0),
            bar_index=1,
        )

        trade = portfolio.trades[0]
        assert trade.gross_pnl == pytest.approx(10.0)
        assert trade.pnl == pytest.approx(-5.0)
        assert trade.is_win is False

    def test_return_pct_is_relative_to_entry_notional(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)
        portfolio.apply_fill(
            make_fill(side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=5.0),
            bar_index=0,
        )
        portfolio.apply_fill(
            make_fill(side=OrderSide.SELL, quantity=4.0, fill_price=110.0, commission=3.0),
            bar_index=1,
        )

        trade = portfolio.trades[0]
        # net = 35 (see above), entry_notional = 100 * 4 = 400
        assert trade.return_pct == pytest.approx(35.0 / 400.0)

    def test_commission_slippage_and_notional_accumulate_across_fills(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)

        # slippage_cost = |fill_price - reference_price| * quantity
        portfolio.apply_fill(
            make_fill(
                side=OrderSide.BUY,
                quantity=10.0,
                reference_price=100.0,
                fill_price=100.5,
                commission=2.0,
            ),
            bar_index=0,
        )
        portfolio.apply_fill(
            make_fill(
                side=OrderSide.SELL,
                quantity=5.0,
                reference_price=100.0,
                fill_price=99.7,
                commission=1.0,
            ),
            bar_index=1,
        )

        # slippage: |100.5-100|*10=5.0, |99.7-100|*5=1.5 -> total 6.5
        assert portfolio.total_commission == pytest.approx(3.0)
        assert portfolio.total_slippage == pytest.approx(6.5)
        # notional: |10*100.5| + |5*99.7| = 1005 + 498.5 = 1503.5
        assert portfolio.total_traded_notional == pytest.approx(1_503.5)


class TestMarkToMarket:
    def test_equity_and_unrealized_pnl_for_long_and_short(self) -> None:
        portfolio = Portfolio(initial_cash=100_000.0)
        # Open a long in A and a short in B, zero cost.
        portfolio.apply_fill(
            make_fill(
                symbol="A", side=OrderSide.BUY, quantity=10.0, fill_price=100.0, commission=0.0
            ),
            bar_index=0,
        )
        portfolio.apply_fill(
            make_fill(
                symbol="B", side=OrderSide.SELL, quantity=5.0, fill_price=50.0, commission=0.0
            ),
            bar_index=0,
        )

        # cash: 100_000 - 1000 (buy A) + 250 (sell B) = 99_250
        assert portfolio.cash == pytest.approx(99_250.0)

        prices = {"A": 110.0, "B": 40.0}
        # market value = 10*110 + (-5)*40 = 1100 - 200 = 900
        assert portfolio.market_value(prices) == pytest.approx(900.0)
        # equity = 99_250 + 900 = 100_150
        assert portfolio.equity(prices) == pytest.approx(100_150.0)

        # unrealized: A = (110-100)*10 = 100; B = (40-50)*(-5) = 50; total 150
        assert portfolio.unrealized_pnl(prices) == pytest.approx(150.0)


class TestAffordability:
    def test_can_afford_refuses_a_buy_beyond_cash(self) -> None:
        portfolio = Portfolio(initial_cash=1_000.0)

        # 10 * 100 + 5 = 1005 > 1000
        assert portfolio.can_afford(OrderSide.BUY, 10.0, 100.0, 5.0) is False
        # 9 * 100 + 5 = 905 <= 1000
        assert portfolio.can_afford(OrderSide.BUY, 9.0, 100.0, 5.0) is True

    def test_sells_always_pass(self) -> None:
        portfolio = Portfolio(initial_cash=1_000.0)

        assert portfolio.can_afford(OrderSide.SELL, 1_000_000.0, 100.0, 5.0) is True

    def test_max_affordable_quantity_accounts_for_commission_and_is_whole(self) -> None:
        portfolio = Portfolio(initial_cash=1_000.0)

        # per_unit = 100 * 1.03 = 103; 1000 / 103 = 9.708... -> 9
        assert portfolio.max_affordable_quantity(100.0, 0.03) == 9.0

    def test_max_affordable_quantity_is_zero_for_non_positive_price(self) -> None:
        portfolio = Portfolio(initial_cash=1_000.0)

        assert portfolio.max_affordable_quantity(0.0, 0.03) == 0.0
        assert portfolio.max_affordable_quantity(-10.0, 0.03) == 0.0


class TestConstruction:
    def test_zero_initial_cash_raises(self) -> None:
        with pytest.raises(ValueError, match="initial_cash must be positive"):
            Portfolio(initial_cash=0.0)

    def test_negative_initial_cash_raises(self) -> None:
        with pytest.raises(ValueError, match="initial_cash must be positive"):
            Portfolio(initial_cash=-100.0)
