from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

CENT = Decimal("0.01")


def dec(value: str | int | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def bps_amount(amount: str | Decimal, basis_points: int) -> Decimal:
    if basis_points < 0:
        raise ValueError("NEGATIVE_BPS")
    return (Decimal(str(amount)) * Decimal(basis_points) / Decimal(10000)).quantize(CENT, rounding=ROUND_HALF_UP)


class TreasuryAdapter(ABC):
    name = "abstract"

    @abstractmethod
    def collect_platform_fee(self, *, opportunity_id: str, amount: Decimal, currency: str, authorization_hash: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class RevenueAccrual:
    opportunity_id: str
    currency: str
    deal_volume: Decimal
    origination_fee: Decimal
    spread_income: Decimal
    total_platform_revenue: Decimal
    authorization_hash: str


class RevenueEngine:
    def __init__(self, ledger_append: Callable[[str, dict], object], treasury: TreasuryAdapter | None = None):
        self._ledger_append = ledger_append
        self._treasury = treasury

    def calculate(self, *, opportunity_id: str, deal_volume: str | Decimal, net_yield: str | Decimal,
                  origination_bps: int = 150, spread_bps: int = 0, currency: str = "KES",
                  authorization_hash: str) -> RevenueAccrual:
        volume = dec(deal_volume)
        yield_value = dec(net_yield)
        fee = bps_amount(volume, origination_bps)
        spread = bps_amount(yield_value, spread_bps)
        total = (fee + spread).quantize(CENT, rounding=ROUND_HALF_UP)
        if total <= 0:
            raise ValueError("REVENUE_NOT_POSITIVE")
        accrual = RevenueAccrual(
            opportunity_id=opportunity_id,
            currency=currency.upper(),
            deal_volume=volume,
            origination_fee=fee,
            spread_income=spread,
            total_platform_revenue=total,
            authorization_hash=authorization_hash,
        )
        self._ledger_append("PLATFORM_FEE_ACCRUED", {
            "opportunityId": opportunity_id,
            "dealVolume": str(volume),
            "originationFee": str(fee),
            "spreadIncome": str(spread),
            "totalPlatformRevenue": str(total),
            "currency": currency.upper(),
            "authorizationHash": authorization_hash,
        })
        return accrual

    def collect_before_principal_release(self, accrual: RevenueAccrual) -> str:
        if self._treasury is None:
            raise RuntimeError("TREASURY_ADAPTER_REQUIRED")
        receipt = self._treasury.collect_platform_fee(
            opportunity_id=accrual.opportunity_id,
            amount=accrual.total_platform_revenue,
            currency=accrual.currency,
            authorization_hash=accrual.authorization_hash,
        )
        if not receipt:
            raise RuntimeError("TREASURY_CONFIRMATION_REQUIRED")
        self._ledger_append("PLATFORM_FEE_SETTLED", {
            "opportunityId": accrual.opportunity_id,
            "receipt": receipt,
            "amount": str(accrual.total_platform_revenue),
            "currency": accrual.currency,
            "authorizationHash": accrual.authorization_hash,
        })
        self._ledger_append("PRINCIPAL_RELEASE_AUTHORIZED", {
            "opportunityId": accrual.opportunity_id,
            "feeReceipt": receipt,
            "authorizationHash": accrual.authorization_hash,
        })
        return receipt
