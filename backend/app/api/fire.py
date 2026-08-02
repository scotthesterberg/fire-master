from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.engines.fire_projections import FireProjectionsEngine
from app.engines.net_worth import NetWorthEngine
from app.engines.spending import SpendingEngine
from app.models.fire_config import FireConfig
from app.models.fire_scenario import FireScenario
from app.models.goal import Goal
from app.models.income_source import IncomeSource
from app.schemas.fire import (
    FireConfigResponse,
    FireConfigUpdate,
    FireMetricsResponse,
    FireNumberResponse,
    FireScenarioCreate,
    FireScenarioResponse,
    FireScenarioUpdate,
    GoalCreate,
    GoalResponse,
    GoalUpdate,
    IncomeSourceCreate,
    IncomeSourceResponse,
    IncomeSourceUpdate,
    IncomeSummaryResponse,
    LifetimeProjectionResponse,
    BridgeStatus,
    MilestonesResponse,
    ReadinessResponse,
    WealthPoolProjection,
    RetirementTimelineResponse,
    ScenarioComparison,
    ScenarioInput,
    SpendingBreakdown,
    SpendingSensitivityPoint,
    SpendingSensitivityResponse,
)

router = APIRouter(prefix="/api/fire", tags=["fire"])


# --- FIRE Config ---


@router.get("/config", response_model=FireConfigResponse)
async def get_fire_config(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    config = await engine.get_or_create_config()
    await db.commit()
    return config


@router.patch("/config", response_model=FireConfigResponse)
async def update_fire_config(
    data: FireConfigUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm.attributes import flag_modified

    engine = FireProjectionsEngine(db)
    config = await engine.get_or_create_config()
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)
    # JSONB columns need explicit dirty-flagging for SQLAlchemy change detection
    if "custom_assumptions" in update_data:
        flag_modified(config, "custom_assumptions")
    await db.commit()
    await db.refresh(config)
    return config


# --- Income Sources ---


@router.get("/income", response_model=list[IncomeSourceResponse])
async def list_income_sources(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IncomeSource).order_by(IncomeSource.annual_amount.desc())
    )
    sources = result.scalars().all()
    return [IncomeSourceResponse.from_model(s) for s in sources]


@router.post("/income", response_model=IncomeSourceResponse)
async def create_income_source(
    data: IncomeSourceCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = IncomeSource(**data.model_dump())
    db.add(source)
    await db.commit()
    return IncomeSourceResponse.from_model(source)


@router.put("/income/{source_id}", response_model=IncomeSourceResponse)
async def update_income_source(
    source_id: UUID,
    data: IncomeSourceUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(IncomeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(source, field, value)
    await db.commit()
    return IncomeSourceResponse.from_model(source)


@router.delete("/income/{source_id}")
async def delete_income_source(
    source_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(IncomeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")
    await db.delete(source)
    await db.commit()
    return {"ok": True}


@router.get("/income/summary", response_model=IncomeSummaryResponse)
async def income_summary(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IncomeSource).where(IncomeSource.is_active == True)
    )
    sources = result.scalars().all()
    total_annual = sum(s.annual_amount for s in sources)
    by_type: dict[str, float] = {}
    for s in sources:
        key = s.income_type.value
        by_type[key] = by_type.get(key, 0) + s.annual_amount / 100

    return IncomeSummaryResponse(
        sources=[IncomeSourceResponse.from_model(s) for s in sources],
        total_annual_cents=total_annual,
        total_monthly_cents=total_annual // 12,
        by_type=by_type,
    )


# --- Goals ---


@router.get("/goals", response_model=list[GoalResponse])
async def list_goals(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Goal).order_by(Goal.created_at.desc()))
    return list(result.scalars().all())


@router.post("/goals", response_model=GoalResponse)
async def create_goal(
    data: GoalCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = Goal(**data.model_dump())
    db.add(goal)
    await db.commit()
    return goal


@router.put("/goals/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: UUID,
    data: GoalUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = await db.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(goal, field, value)
    await db.commit()
    return goal


@router.delete("/goals/{goal_id}")
async def delete_goal(
    goal_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = await db.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    await db.delete(goal)
    await db.commit()
    return {"ok": True}


# --- Projections ---


@router.get("/number", response_model=FireNumberResponse)
async def get_fire_number(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.compute_fire_number()


@router.get("/milestones", response_model=MilestonesResponse)
async def get_milestones(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.compute_milestones()


@router.get("/bridge-status", response_model=BridgeStatus)
async def get_bridge_status(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.compute_bridge_status()


@router.get("/wealth-projection", response_model=WealthPoolProjection)
async def get_wealth_projection(
    end_age: int = 82,
    bridge_months: int = 0,
    spending_override: int | None = None,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.project_wealth_pools(
        end_age, bridge_months=bridge_months,
        spending_override_cents=spending_override,
    )


@router.get("/spending-sensitivity", response_model=SpendingSensitivityResponse)
async def spending_sensitivity(
    step: int = 100_000,
    levels: int = 5,
    end_age: int = 82,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run projections at multiple spending levels and return comparison."""
    engine = FireProjectionsEngine(db)
    config = await engine.get_effective_config()
    base_spending = await engine._get_annual_spending(config)
    healthcare = config.healthcare_monthly_cost or 0
    ca = config.custom_assumptions or {}

    # Extract housing ingredients from effective config (scenario-aware).
    # Check generic property_sales list first, falling back to legacy single-property keys
    # (miami_sale / park_city / sauvie_sale) for backwards compatibility.
    property_sales = ca.get("property_sales", []) or []
    primary_sale = next((s for s in property_sales if s.get("bucket") in ("primary", "miami") or s.get("re_bucket") in ("primary", "miami") or s.get("key") in ("primary", "primary_residence")), None)
    income_sale = next((s for s in property_sales if s.get("bucket") in ("income", "sauvie") or s.get("re_bucket") in ("income", "sauvie") or s.get("key") in ("income", "income_property")), None)
    secondary_sale = next((s for s in property_sales if s.get("bucket") in ("secondary", "park_city") or s.get("re_bucket") in ("secondary", "park_city") or s.get("key") in ("secondary", "secondary_property")), None)

    miami_cfg = ca.get("miami_sale", {})
    pc_cfg = ca.get("park_city", {})
    proj_cfg = ca.get("projection", {})
    primary_pi = proj_cfg.get("primary_property_mortgage_pi", 0)

    if primary_sale:
        primary_all_in = primary_sale.get("monthly_cost", primary_pi)
        post_sale_rent = primary_sale.get("post_sale_rent", 0)
    else:
        primary_all_in = miami_cfg.get("monthly_cost", primary_pi if primary_pi > 0 else 0)
        post_sale_rent = miami_cfg.get("post_sale_rent", 0)

    if primary_all_in == 0 and primary_pi > 0:
        primary_all_in = primary_pi

    if income_sale:
        income_property_cost = income_sale.get("monthly_cost", 0)
    else:
        income_property_cost = ca.get("sauvie_sale", {}).get("monthly_cost_saved", 0)

    if secondary_sale:
        secondary_property_cost = secondary_sale.get("monthly_cost", 0)
    else:
        secondary_property_cost = pc_cfg.get("monthly_cost", 0)

    base_monthly = round(base_spending / 12 / 100, 0)
    non_housing = max(0, base_monthly - primary_all_in - income_property_cost - secondary_property_cost)

    # Center on current spending, ± levels//2 steps
    half = levels // 2
    points = []
    for i in range(-half, levels - half):
        override = base_spending + i * step * 12  # step is monthly cents, convert to annual
        result = await engine.project_wealth_pools(
            end_age=end_age, spending_override_cents=override,
        )
        points.append(SpendingSensitivityPoint(
            monthly_spending=round(override / 12 / 100, 0),
            total_at_end=round(result.total_at_end, 0),
            cash_zero_month=result.cash_zero_month,
        ))

    return SpendingSensitivityResponse(
        current_monthly=base_monthly,
        base_monthly=base_monthly,
        healthcare_monthly=round(healthcare / 100, 0),
        breakdown=SpendingBreakdown(
            primary_property_all_in=primary_all_in,
            primary_property_pi=primary_pi,
            income_property_cost=income_property_cost,
            secondary_property_cost=secondary_property_cost,
            non_housing=non_housing,
            post_sale_rent=post_sale_rent,
        ),
        points=points,
    )


@router.get("/readiness", response_model=ReadinessResponse)
async def get_readiness(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.compute_readiness()


@router.get("/timeline", response_model=RetirementTimelineResponse)
async def get_timeline(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.project_timeline()


@router.get("/lifetime", response_model=LifetimeProjectionResponse)
async def get_lifetime_projection(
    scenario: str = "moderate",
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.project_lifetime(scenario)


# --- Scenarios ---


@router.post("/scenario", response_model=ScenarioComparison)
async def run_scenario(
    scenario_input: ScenarioInput,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = FireProjectionsEngine(db)
    return await engine.run_scenario(scenario_input)


# --- Named Scenarios (saved assumption overrides) ---


@router.get("/scenarios", response_model=list[FireScenarioResponse])
async def list_scenarios(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FireScenario).order_by(FireScenario.is_active.desc(), FireScenario.updated_at.desc())
    )
    return result.scalars().all()


@router.post("/scenarios", response_model=FireScenarioResponse, status_code=201)
async def create_scenario(
    data: FireScenarioCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # If this scenario should be active, deactivate others first
    if data.is_active:
        result = await db.execute(select(FireScenario).where(FireScenario.is_active == True))
        for s in result.scalars().all():
            s.is_active = False

    scenario = FireScenario(
        name=data.name,
        description=data.description,
        overrides=data.overrides,
        is_active=data.is_active,
    )
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return scenario


@router.put("/scenarios/{scenario_id}", response_model=FireScenarioResponse)
async def update_scenario(
    scenario_id: UUID,
    data: FireScenarioUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FireScenario).where(FireScenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(scenario, field, value)
    await db.commit()
    await db.refresh(scenario)
    return scenario


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FireScenario).where(FireScenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    await db.delete(scenario)
    await db.commit()
    return {"ok": True}


@router.post("/scenarios/{scenario_id}/activate", response_model=FireScenarioResponse)
async def activate_scenario(
    scenario_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Activate a scenario — deactivates all others first."""
    result = await db.execute(
        select(FireScenario).where(FireScenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Deactivate all
    all_result = await db.execute(select(FireScenario).where(FireScenario.is_active == True))
    for s in all_result.scalars().all():
        s.is_active = False

    # Activate this one
    scenario.is_active = True
    await db.commit()
    await db.refresh(scenario)
    return scenario


@router.post("/scenarios/deactivate")
async def deactivate_all_scenarios(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate all scenarios — revert to base config."""
    result = await db.execute(select(FireScenario).where(FireScenario.is_active == True))
    for s in result.scalars().all():
        s.is_active = False
    await db.commit()
    return {"ok": True}


@router.get("/scenarios/{scenario_id}/preview", response_model=WealthPoolProjection)
async def preview_scenario(
    scenario_id: UUID,
    end_age: int = 82,
    bridge_months: int = 0,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview wealth projection for a specific scenario without activating it."""
    result = await db.execute(
        select(FireScenario).where(FireScenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    engine = FireProjectionsEngine(db)
    return await engine.project_wealth_pools_for_scenario(
        scenario_id, end_age=end_age, bridge_months=bridge_months
    )


# --- Power endpoint ---


@router.get("/metrics", response_model=FireMetricsResponse)
async def get_fire_metrics(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All FIRE metrics in one call — the Claude Code power endpoint."""
    engine = FireProjectionsEngine(db)
    spending_engine = SpendingEngine(db)

    fire_number = await engine.compute_fire_number()
    readiness = await engine.compute_readiness()

    # Timeline (may be None if config not set)
    config = await engine.get_or_create_config()
    timeline = None
    if config.date_of_birth and config.target_retirement_age:
        timeline = await engine.project_timeline()

    # Income summary
    result = await db.execute(
        select(IncomeSource).where(IncomeSource.is_active == True)
    )
    sources = result.scalars().all()
    total_annual = sum(s.annual_amount for s in sources)
    by_type: dict[str, float] = {}
    for s in sources:
        key = s.income_type.value
        by_type[key] = by_type.get(key, 0) + s.annual_amount / 100

    income_summary = IncomeSummaryResponse(
        sources=[IncomeSourceResponse.from_model(s) for s in sources],
        total_annual_cents=total_annual,
        total_monthly_cents=total_annual // 12,
        by_type=by_type,
    )

    # Savings rate
    savings_data = await spending_engine.get_savings_rate(months=12)

    # Goals
    goal_result = await db.execute(select(Goal).order_by(Goal.created_at.desc()))
    goals = list(goal_result.scalars().all())

    # Net worth growth
    nw_engine = NetWorthEngine(db)
    nw = await nw_engine.calculate_current()

    return FireMetricsResponse(
        fire_number=fire_number,
        readiness=readiness,
        timeline=timeline,
        income_summary=income_summary,
        savings_rate_current=savings_data.current_rate,
        savings_rate_average=savings_data.average_rate,
        net_worth_growth_30d=nw.change_30d,
        net_worth_growth_1y=None,  # TODO: compute from snapshots
        goals=goals,
    )
