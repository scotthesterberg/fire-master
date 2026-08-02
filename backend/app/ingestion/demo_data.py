"""Demo-persona data lifecycle helpers — the single source of truth for detecting
and clearing the built-in demo persona.

Every row the demo seeder (scripts/seed_demo.py) creates is marked
``custom_data.demo_seed = true`` and ``source = MANUAL``, so it is invisible to
Monarch sync/reconcile and can be removed without ever touching real data. Both
the manual ``seed_demo.py --remove`` and the automatic "going live" clear (run by
the Monarch sync) go through here, so there is exactly one removal implementation.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.net_worth import NetWorthEngine
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot
from app.models.cashflow_event import CashflowEvent
from app.models.enums import DataSource
from app.models.fire_config import FireConfig
from app.models.fire_scenario import FireScenario
from app.models.income_source import IncomeSource
from app.models.net_worth_snapshot import NetWorthSnapshot
from app.models.property import Property
from app.models.transaction import Transaction


def _demo_filter(model):
    """SQLAlchemy predicate matching only demo-seeded rows of ``model``."""
    return model.custom_data["demo_seed"].as_boolean() == True  # noqa: E712


async def has_demo_rows(db: AsyncSession) -> bool:
    """True if any demo-seeded account remains."""
    row = await db.execute(select(Account).where(_demo_filter(Account)).limit(1))
    return row.scalar_one_or_none() is not None


async def has_real_accounts(db: AsyncSession) -> bool:
    """True if any real (Monarch-synced) account exists."""
    row = await db.execute(
        select(Account).where(Account.source == DataSource.MONARCH).limit(1)
    )
    return row.scalar_one_or_none() is not None


async def clear_demo_data(db: AsyncSession) -> dict:
    """Delete every demo-seeded row (accounts + their balance history, income
    sources, cashflow events, demo scenarios) and rebuild net-worth history from whatever real
    data remains.

    Marker-scoped via ``_demo_filter`` — it can NEVER touch real (Monarch) rows.
    Does not commit; the caller owns the transaction. Returns a count summary.
    """
    demo_accounts = (
        await db.execute(select(Account).where(_demo_filter(Account)))
    ).scalars().all()
    demo_ids = [a.id for a in demo_accounts]
    removed_txns = 0
    if demo_ids:
        await db.execute(
            delete(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(demo_ids))
        )
        # Demo transactions live on demo accounts (FK cascade would remove them
        # anyway); delete explicitly so the summary reports an honest count.
        removed_txns = int((await db.execute(
            select(func.count()).select_from(Transaction)
            .where(Transaction.account_id.in_(demo_ids))
        )).scalar() or 0)
        await db.execute(delete(Transaction).where(Transaction.account_id.in_(demo_ids)))
    for acct in demo_accounts:
        await db.delete(acct)

    # Demo properties (their rules cascade; any real transaction a demo rule
    # classified reverts to unassigned via the FK's SET NULL + next reclassify).
    demo_props = (
        await db.execute(
            select(Property).where(Property.extra_data["demo_seed"].as_boolean() == True)  # noqa: E712
        )
    ).scalars().all()
    for prop in demo_props:
        await db.delete(prop)

    removed_income = 0
    for src in (
        await db.execute(select(IncomeSource).where(_demo_filter(IncomeSource)))
    ).scalars().all():
        await db.delete(src)
        removed_income += 1

    removed_events = 0
    for ev in (
        await db.execute(select(CashflowEvent).where(_demo_filter(CashflowEvent)))
    ).scalars().all():
        await db.delete(ev)
        removed_events += 1

    # Demo scenarios
    demo_scenario_names = [
        "Baseline — Sell Casita, Downsize at Year 5",
        "Keep the Desert Casita",
        "Downsize the Loft Now",
        "Trim Spending to $11K/mo",
    ]
    all_scenarios = (await db.execute(select(FireScenario))).scalars().all()
    removed_scenarios = 0
    for sc in all_scenarios:
        is_demo = sc.name in demo_scenario_names
        if not is_demo and sc.overrides:
            ov_ca = sc.overrides.get("custom_assumptions", {})
            ov_props = ov_ca.get("property_sales", [])
            if any(p.get("key") in ("desert_casita", "city_loft") for p in ov_props):
                is_demo = True
        if is_demo:
            await db.delete(sc)
            removed_scenarios += 1

    # Clean demo persona markers and demo properties from FIRE config custom_assumptions
    cfg_row = (await db.execute(select(FireConfig).limit(1))).scalar_one_or_none()
    if cfg_row is not None and cfg_row.custom_assumptions:
        ca = dict(cfg_row.custom_assumptions)
        if ca.get("demo_persona"):
            ca.pop("demo_persona", None)
            if "property_sales" in ca:
                ca["property_sales"] = [
                    p for p in ca["property_sales"]
                    if p.get("key") not in ("desert_casita", "city_loft")
                ]
            cfg_row.custom_assumptions = ca

    await db.flush()

    # Rebuild aggregate net-worth history from whatever balance data is left.
    await db.execute(delete(NetWorthSnapshot))
    remaining = (await db.execute(select(BalanceSnapshot).limit(1))).scalar_one_or_none()
    nw_snapshots = await NetWorthEngine(db).backfill_snapshots() if remaining is not None else 0

    return {
        "accounts": len(demo_accounts),
        "transactions": removed_txns,
        "properties": len(demo_props),
        "income_sources": removed_income,
        "cashflow_events": removed_events,
        "scenarios": removed_scenarios,
        "net_worth_snapshots": nw_snapshots,
    }
