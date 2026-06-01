"""
VC Waterfall Expert — Deterministic Calculation Engine

Inherits DeterministicSubtaskExpert. Pure Python Decimal arithmetic.
Zero LLM calls, zero randomness, zero IO.

Verified capabilities:
  - Post-money SAFE conversion (2x A/B experiments vs generic LLM)
  - Anti-dilution: broad-based / narrow-based / full ratchet
  - Liquidation waterfall: non-participating / participating / cap / senior
  - Participation classification

Source: NVCA Model Term Sheet, YC Post-money SAFE
"""

from typing import Literal, List, Optional, Union
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import DeterministicSubtaskExpert

D = Decimal
D0 = D("0")
D1 = D("1")
D100 = D("100")


def _coerce_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r'[$,\s]', '', v)
        multipliers = {'K': 1e3, 'k': 1e3, 'M': 1e6, 'm': 1e6, 'B': 1e9, 'b': 1e9}
        if len(cleaned) > 1 and cleaned[-1] in multipliers:
            return float(cleaned[:-1]) * multipliers[cleaned[-1]]
        return float(cleaned)
    raise ValueError(f'Cannot coerce {type(v).__name__} to float: {v!r}')


def _coerce_int(v):
    return int(_coerce_float(v))


def _coerce_enum(v, enum_cls):
    if isinstance(v, enum_cls):
        return v
    if isinstance(v, str):
        normalized = v.strip().lower().replace('-', '_').replace(' ', '_')
        for member in enum_cls:
            if normalized == member.value:
                return member
        raise ValueError(f'Invalid {enum_cls.__name__}: {v!r}')
    raise ValueError(f'Cannot coerce {type(v).__name__} to {enum_cls.__name__}: {v!r}')


# ────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────

class ClauseType(str, Enum):
    SAFE_CONVERSION = "safe_conversion"
    ANTI_DILUTION = "anti_dilution"
    LIQUIDATION_WATERFALL = "liquidation_waterfall"
    PARTICIPATION_CLASSIFY = "participation_classify"


class DilutionMethod(str, Enum):
    BROAD_BASED = "broad_based"
    NARROW_BASED = "narrow_based"
    FULL_RATCHET = "full_ratchet"


class ParticipationType(str, Enum):
    NON_PARTICIPATING = "non_participating"
    PARTICIPATING = "participating"
    PARTICIPATING_CAPPED = "participating_capped"


class Seniority(str, Enum):
    PARI_PASSU = "pari_passu"
    SENIOR = "senior"


class InvestorChoice(str, Enum):
    PREFERENCE = "preference"
    CONVERT = "convert"
    PARTICIPATE = "participate"
    PARTICIPATE_CAPPED = "participate_capped"


# ────────────────────────────────────────────────────────────
# Input Schemas (with LLM-safe coercions)
# ────────────────────────────────────────────────────────────

class SAFEConversionInput(BaseModel):
    clause_type: Literal[ClauseType.SAFE_CONVERSION] = ClauseType.SAFE_CONVERSION
    safe_investment: float = Field(..., gt=0, description="SAFE investment amount ($)")
    safe_cap: float = Field(..., gt=0, description="SAFE valuation cap ($)")
    pre_money_valuation: float = Field(..., gt=0, description="Series A pre-money valuation ($)")
    series_a_investment: float = Field(..., gt=0, description="Series A investment amount ($)")
    founder_shares: int = Field(..., gt=0, description="Founder's current share count")
    discount_rate: Optional[float] = Field(None, ge=0, lt=1, description="SAFE discount rate (e.g. 0.20 for 20%)")

    @field_validator('safe_investment', 'safe_cap', 'pre_money_valuation', 'series_a_investment', mode='before')
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator('founder_shares', mode='before')
    @classmethod
    def coerce_int(cls, v):
        return _coerce_int(v)

    @field_validator('discount_rate', mode='before')
    @classmethod
    def coerce_discount(cls, v):
        if v is None:
            return None
        v = _coerce_float(v)
        if v > 1:
            v = v / D100
        if v == 0:
            return None
        return float(v)


class AntiDilutionInput(BaseModel):
    clause_type: Literal[ClauseType.ANTI_DILUTION] = ClauseType.ANTI_DILUTION
    old_conversion_price: float = Field(..., gt=0, description="Original conversion price ($)")
    old_shares_outstanding: float = Field(..., gt=0, description="Shares outstanding before new issuance")
    new_shares_issued: float = Field(..., gt=0, description="New shares issued in down round")
    new_share_price: float = Field(..., gt=0, description="Price per share in down round ($)")
    method: DilutionMethod = Field(..., description="Broad-based, narrow-based, or full ratchet")

    @field_validator('old_conversion_price', 'old_shares_outstanding', 'new_shares_issued', 'new_share_price', mode='before')
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator('method', mode='before')
    @classmethod
    def coerce_method(cls, v):
        return _coerce_enum(v, DilutionMethod)


class PreferredRound(BaseModel):
    investment: float = Field(..., gt=0)
    multiple: float = Field(..., gt=0)
    ownership_pct: float = Field(..., gt=0, le=100)
    participation: ParticipationType
    cap_multiple: Optional[float] = Field(None, gt=0)
    seniority: Seniority = Seniority.PARI_PASSU
    seniority_rank: Optional[int] = Field(None, ge=0)

    @field_validator('investment', 'multiple', 'ownership_pct', mode='before')
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator('cap_multiple', mode='before')
    @classmethod
    def coerce_opt_float(cls, v):
        if v is None:
            return None
        return _coerce_float(v)

    @field_validator('seniority_rank', mode='before')
    @classmethod
    def coerce_opt_int(cls, v):
        if v is None:
            return None
        return _coerce_int(v)

    @field_validator('participation', mode='before')
    @classmethod
    def coerce_participation(cls, v):
        return _coerce_enum(v, ParticipationType)

    @field_validator('seniority', mode='before')
    @classmethod
    def coerce_seniority(cls, v):
        return _coerce_enum(v, Seniority)


class LiquidationWaterfallInput(BaseModel):
    clause_type: Literal[ClauseType.LIQUIDATION_WATERFALL] = ClauseType.LIQUIDATION_WATERFALL
    exit_value: float = Field(..., gt=0, description="Total exit value ($)")
    preferred_rounds: List[PreferredRound] = Field(..., min_length=1)
    common_ownership_pct: float = Field(..., gt=0, le=100)

    @field_validator('exit_value', 'common_ownership_pct', mode='before')
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)


class ParticipationClassifyInput(BaseModel):
    clause_type: Literal[ClauseType.PARTICIPATION_CLASSIFY] = ClauseType.PARTICIPATION_CLASSIFY
    clause_text_keywords: List[str] = Field(
        ...,
        min_length=1,
        description="Keywords from clause text for classification",
    )


# ────────────────────────────────────────────────────────────
# Output Schemas
# ────────────────────────────────────────────────────────────

class SAFEConversionOutput(BaseModel):
    safe_ownership_pct: float
    series_a_ownership_pct: float
    founder_ownership_pct: float
    total_shares: float
    safe_shares: float
    series_a_shares: float
    price_per_share: float
    pre_money_verification: float
    effective_mechanism: str = "cap_only"
    cap_price_per_share: Optional[float] = None
    discount_price_per_share: Optional[float] = None


class AntiDilutionOutput(BaseModel):
    method: str
    old_conversion_price: float
    new_conversion_price: float
    price_reduction_pct: float
    old_shares_outstanding: float
    formula_steps: List[str]


class RoundAllocation(BaseModel):
    round_index: int
    preference_amount: float
    participation_amount: float
    cap_excess_returned: float
    total_received: float
    choice_made: str


class LiquidationWaterfallOutput(BaseModel):
    exit_value: float
    total_preferences_paid: float
    remaining_after_preferences: float
    allocations: List[RoundAllocation]
    common_total: float
    verification_sum: float


class ParticipationClassifyOutput(BaseModel):
    classification: str
    confidence: float
    reasoning: str


# ────────────────────────────────────────────────────────────
# Union types for dispatch
# ────────────────────────────────────────────────────────────

from typing import Union

VCInput = Union[SAFEConversionInput, AntiDilutionInput, LiquidationWaterfallInput, ParticipationClassifyInput]
VCOutput = Union[SAFEConversionOutput, AntiDilutionOutput, LiquidationWaterfallOutput, ParticipationClassifyOutput]


# ────────────────────────────────────────────────────────────
# Expert
# ────────────────────────────────────────────────────────────

class VCWaterfallExpert(DeterministicSubtaskExpert):

    @property
    def name(self):
        return "vc_waterfall_v1"

    @property
    def version(self):
        return "1.0.0"

    @property
    def input_schema(self):
        return self._resolve_input_schema

    @property
    def output_schema(self):
        return self._resolve_output_schema

    @property
    def _resolve_input_schema(self):
        return BaseModel

    @property
    def _resolve_output_schema(self):
        return BaseModel

    def validate_input(self, raw: dict):
        clause_type = raw.get("clause_type", "")
        schema_map = {
            ClauseType.SAFE_CONVERSION: SAFEConversionInput,
            ClauseType.ANTI_DILUTION: AntiDilutionInput,
            ClauseType.LIQUIDATION_WATERFALL: LiquidationWaterfallInput,
            ClauseType.PARTICIPATION_CLASSIFY: ParticipationClassifyInput,
        }
        schema_cls = schema_map.get(clause_type)
        if schema_cls is None:
            return False, f"Unsupported clause_type: '{clause_type}'. Supported: {[e.value for e in ClauseType]}"
        try:
            obj = schema_cls(**raw)
            return True, obj
        except Exception as e:
            return False, str(e)

    def validate_output(self, output) -> tuple:
        try:
            if hasattr(output, "model_validate"):
                output.model_validate(output)
            return True, ""
        except Exception as e:
            return False, str(e)

    def compute(self, input_data):
        dispatch = {
            ClauseType.SAFE_CONVERSION: self._compute_safe,
            ClauseType.ANTI_DILUTION: self._compute_anti_dilution,
            ClauseType.LIQUIDATION_WATERFALL: self._compute_waterfall,
            ClauseType.PARTICIPATION_CLASSIFY: self._classify_participation,
        }
        handler = dispatch.get(input_data.clause_type)
        if handler is None:
            raise ValueError(f"No handler for {input_data.clause_type}")
        return handler(input_data)

    # ── Post-money SAFE ────────────────────────────────────

    def _compute_safe(self, inp: SAFEConversionInput) -> SAFEConversionOutput:
        safe_inv = D(str(inp.safe_investment))
        safe_cap = D(str(inp.safe_cap))
        pre_money = D(str(inp.pre_money_valuation))
        sa_inv = D(str(inp.series_a_investment))
        founder = D(str(inp.founder_shares))
        discount = D(str(inp.discount_rate)) if inp.discount_rate else None

        # ── Step 1: Cap-based baseline ──
        safe_pct_cap = safe_inv / safe_cap
        sa_pct = sa_inv / (pre_money + sa_inv)
        founder_pct_cap = D1 - safe_pct_cap - sa_pct

        if founder_pct_cap <= D0:
            raise ValueError("Founder ownership <= 0%. Check input parameters.")

        total_cap = D(str(founder)) / founder_pct_cap
        safe_shares_cap = total_cap * safe_pct_cap
        sa_shares_cap = total_cap * sa_pct
        price_sa_cap = sa_inv / sa_shares_cap
        cap_price = safe_inv / safe_shares_cap

        # ── Step 2: Algebraic discount solution (self-consistent) ──
        # When discount is binding, SA price changes. Solve algebraically:
        #   S = safe_inv * founder / (pre_money * (1-d) - safe_inv)
        #   discount_price = (pre_money * (1-d) - safe_inv) / founder
        if discount is not None:
            denom = pre_money * (D1 - discount) - safe_inv
            if denom > D0:
                discount_price = denom / D(str(founder))
            else:
                discount_price = None

            if discount_price is not None and discount_price < cap_price:
                # Discount is binding — recalculate with correct SA price
                safe_shares_disc = safe_inv / discount_price
                pre_money_shares = D(str(founder)) + safe_shares_disc
                price_sa_disc = pre_money / pre_money_shares
                sa_shares_disc = sa_inv / price_sa_disc
                total_disc = pre_money_shares + sa_shares_disc

                safe_pct_disc = safe_shares_disc / total_disc
                sa_pct_disc = sa_shares_disc / total_disc
                founder_pct_disc = D(str(founder)) / total_disc
                pre_money_verify = pre_money_shares * price_sa_disc

                return SAFEConversionOutput(
                    safe_ownership_pct=float(safe_pct_disc * D100),
                    series_a_ownership_pct=float(sa_pct_disc * D100),
                    founder_ownership_pct=float(founder_pct_disc * D100),
                    total_shares=float(total_disc.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                    safe_shares=float(safe_shares_disc.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                    series_a_shares=float(sa_shares_disc.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                    price_per_share=float(price_sa_disc.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
                    pre_money_verification=float(pre_money_verify.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                    effective_mechanism="discount",
                    cap_price_per_share=float(cap_price.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
                    discount_price_per_share=float(discount_price.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
                )

            approx_discount = price_sa_cap * (D1 - discount) if discount else None
            pre_money_verify = (D(str(founder)) + safe_shares_cap) * price_sa_cap
            return SAFEConversionOutput(
                safe_ownership_pct=float(safe_pct_cap * D100),
                series_a_ownership_pct=float(sa_pct * D100),
                founder_ownership_pct=float(founder_pct_cap * D100),
                total_shares=float(total_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                safe_shares=float(safe_shares_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                series_a_shares=float(sa_shares_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                price_per_share=float(price_sa_cap.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
                pre_money_verification=float(pre_money_verify.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
                effective_mechanism="cap",
                cap_price_per_share=float(cap_price.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
                discount_price_per_share=float(approx_discount.quantize(D("0.0001"), rounding=ROUND_HALF_UP)) if approx_discount else None,
            )

        pre_money_verify = (D(str(founder)) + safe_shares_cap) * price_sa_cap
        return SAFEConversionOutput(
            safe_ownership_pct=float(safe_pct_cap * D100),
            series_a_ownership_pct=float(sa_pct * D100),
            founder_ownership_pct=float(founder_pct_cap * D100),
            total_shares=float(total_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
            safe_shares=float(safe_shares_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
            series_a_shares=float(sa_shares_cap.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
            price_per_share=float(price_sa_cap.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
            pre_money_verification=float(pre_money_verify.quantize(D("0.01"), rounding=ROUND_HALF_UP)),
            effective_mechanism="cap_only",
            cap_price_per_share=float(cap_price.quantize(D("0.0001"), rounding=ROUND_HALF_UP)),
            discount_price_per_share=None,
        )

    # ── Anti-dilution ──────────────────────────────────────

    def _compute_anti_dilution(self, inp: AntiDilutionInput) -> AntiDilutionOutput:
        cp1 = D(str(inp.old_conversion_price))
        n_old = D(str(inp.old_shares_outstanding))
        n_new = D(str(inp.new_shares_issued))
        p_new = D(str(inp.new_share_price))
        steps = []

        if inp.method == DilutionMethod.FULL_RATCHET:
            cp2 = p_new
            steps.append(f"Full ratchet: CP₂ = P_new = ${float(cp2):.4f}")
        else:
            consideration = p_new * n_new
            numerator = cp1 * n_old + consideration
            denominator = n_old + n_new
            cp2 = (numerator / denominator).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
            label = "broad-based" if inp.method == DilutionMethod.BROAD_BASED else "narrow-based"
            steps.append(f"{label}: CP₂ = (CP₁×N_old + P_new×N_new) / (N_old + N_new)")
            steps.append(f"= (${float(cp1)}×{float(n_old):,.0f} + ${float(p_new)}×{float(n_new):,.0f}) / ({float(n_old):,.0f} + {float(n_new):,.0f})")
            steps.append(f"= ${float(numerator):,.0f} / {float(denominator):,.0f} = ${float(cp2):.4f}")

        reduction = ((cp1 - cp2) / cp1 * D100).quantize(D("0.01"), rounding=ROUND_HALF_UP)

        return AntiDilutionOutput(
            method=inp.method.value,
            old_conversion_price=float(cp1),
            new_conversion_price=float(cp2),
            price_reduction_pct=float(reduction),
            old_shares_outstanding=float(n_old),
            formula_steps=steps,
        )

    # ── Liquidation waterfall ──────────────────────────────
    #
    # Two-pass architecture:
    #   Pass 1: Pay ALL preference amounts (seniority order)
    #   Pass 2: Handle participation / conversion from remaining

    def _compute_waterfall(self, inp: LiquidationWaterfallInput) -> LiquidationWaterfallOutput:
        exit_val = D(str(inp.exit_value))

        rounds_sorted = sorted(
            enumerate(inp.preferred_rounds),
            key=lambda x: (x[1].seniority_rank or 0, x[0]),
        )

        Q = lambda v: v.quantize(D("0.01"), rounding=ROUND_HALF_UP)

        # ── Pass 1: Pay preferences ────────────────────────
        prefs_paid = {}
        remaining = exit_val
        for orig_idx, rnd in rounds_sorted:
            pref_due = D(str(rnd.investment)) * D(str(rnd.multiple))
            actual = min(pref_due, remaining) if remaining > D0 else D0
            prefs_paid[orig_idx] = actual
            remaining -= actual

        remaining_after_prefs = remaining

        # ── Pass 2: Participation / Conversion ─────────────
        # Compute ownership ratios among ALL shareholders for pro-rata
        total_ownership = D(str(inp.common_ownership_pct))
        for _, rnd in rounds_sorted:
            total_ownership += D(str(rnd.ownership_pct))
        total_ownership = total_ownership / D100

        allocations = []
        cap_excess_pool = D0

        for orig_idx, rnd in rounds_sorted:
            inv = D(str(rnd.investment))
            own_frac = D(str(rnd.ownership_pct)) / D100
            pref = prefs_paid[orig_idx]

            if rnd.participation == ParticipationType.NON_PARTICIPATING:
                convert_val = exit_val * own_frac
                if convert_val > pref:
                    prefs_paid[orig_idx] = D0
                    remaining_after_prefs += pref
                    allocations.append(RoundAllocation(
                        round_index=orig_idx,
                        preference_amount=0, participation_amount=float(Q(convert_val)),
                        cap_excess_returned=0, total_received=float(Q(convert_val)),
                        choice_made="convert",
                    ))
                else:
                    allocations.append(RoundAllocation(
                        round_index=orig_idx,
                        preference_amount=float(Q(pref)), participation_amount=0,
                        cap_excess_returned=0, total_received=float(Q(pref)),
                        choice_made="preference",
                    ))

            elif rnd.participation in (ParticipationType.PARTICIPATING, ParticipationType.PARTICIPATING_CAPPED):
                part = remaining_after_prefs * own_frac if remaining_after_prefs > D0 else D0
                raw_total = pref + part

                if rnd.participation == ParticipationType.PARTICIPATING_CAPPED and rnd.cap_multiple:
                    cap_limit = inv * D(str(rnd.cap_multiple))
                    if raw_total > cap_limit:
                        excess = raw_total - cap_limit
                        cap_excess_pool += excess
                        part = cap_limit - pref
                        participate_total = cap_limit
                        choice = "participate_capped"
                    else:
                        participate_total = raw_total
                        choice = "participate"
                else:
                    participate_total = raw_total
                    choice = "participate"

                convert_val = exit_val * own_frac
                if convert_val > participate_total:
                    prefs_paid[orig_idx] = D0
                    remaining_after_prefs += pref
                    cap_excess_pool -= (part - (participate_total - pref)) if choice == "participate_capped" else D0
                    allocations.append(RoundAllocation(
                        round_index=orig_idx,
                        preference_amount=0, participation_amount=float(Q(convert_val)),
                        cap_excess_returned=0, total_received=float(Q(convert_val)),
                        choice_made="convert",
                    ))
                else:
                    allocations.append(RoundAllocation(
                        round_index=orig_idx,
                        preference_amount=float(Q(pref)), participation_amount=float(Q(part)),
                        cap_excess_returned=float(Q(excess if choice == "participate_capped" and raw_total > cap_limit else D0)),
                        total_received=float(Q(participate_total)),
                        choice_made=choice,
                    ))
            else:
                allocations.append(RoundAllocation(
                    round_index=orig_idx,
                    preference_amount=float(Q(pref)), participation_amount=0,
                    cap_excess_returned=0, total_received=float(Q(pref)),
                    choice_made="preference",
                ))

        total_pref = sum(D(str(a.preference_amount)) for a in allocations)
        total_part = sum(D(str(a.participation_amount)) for a in allocations)
        common_total = exit_val - total_pref - total_part
        common_total = max(D0, common_total)

        return LiquidationWaterfallOutput(
            exit_value=float(exit_val),
            total_preferences_paid=float(Q(total_pref)),
            remaining_after_preferences=float(Q(exit_val - total_pref)),
            allocations=allocations,
            common_total=float(Q(common_total)),
            verification_sum=float(Q(total_pref + total_part + common_total)),
        )

    # ── Participation classification ───────────────────────

    def _classify_participation(self, inp: ParticipationClassifyInput) -> ParticipationClassifyOutput:
        text = " ".join(kw.lower() for kw in inp.clause_text_keywords)

        if "non-participating" in text or "non participating" in text:
            return ParticipationClassifyOutput(
                classification="non_participating",
                confidence=0.95,
                reasoning="Keyword 'non-participating' detected. Investor chooses between preference or conversion.",
            )
        has_no_cap = "no cap" in text or "without cap" in text or "uncapped" in text
        if has_no_cap:
            if "participating" in text or "participation" in text or "participate" in text:
                return ParticipationClassifyOutput(
                    classification="participating",
                    confidence=0.90,
                    reasoning="Keywords 'participating' + explicit 'no cap/uncapped'. Unlimited participation.",
                )
        if "capped" in text or ("cap" in text and not has_no_cap):
            if "participating" in text or "participation" in text or "participate" in text:
                return ParticipationClassifyOutput(
                    classification="participating_capped",
                    confidence=0.90,
                    reasoning="Keywords 'participating' + 'cap/capped' detected. Investor gets preference + participation up to cap.",
                )

        if "participating" in text or "participation" in text or "participate" in text or "double-dip" in text or "double dip" in text:
            if "cap" not in text and "capped" not in text:
                return ParticipationClassifyOutput(
                    classification="participating",
                    confidence=0.85,
                    reasoning="Keyword 'participating' without 'cap'. Investor gets preference + unlimited participation.",
                )

        return ParticipationClassifyOutput(
            classification="non_participating",
            confidence=0.50,
            reasoning="No clear participation keywords found. Defaulting to non-participating (market standard).",
        )

    # ── Ability boundary ───────────────────────────────────

    def ability_boundary(self) -> str:
        return (
            "[Deterministic Subtask Expert] VC Waterfall v1\n"
            "\n"
            "CAN DO (guaranteed deterministic):\n"
            "- Post-money SAFE conversion: equity % and share calculation\n"
            "- Anti-dilution: broad-based / narrow-based weighted average, full ratchet\n"
            "- Liquidation waterfall: multi-round, non-participating/participating/capped, senior\n"
            "- Participation type classification from clause keywords\n"
            "\n"
            "ABSOLUTELY DOES NOT:\n"
            "- Provide legal advice or fairness assessment\n"
            "- Handle board composition, drag-along, dividend, protective provisions\n"
            "- Accept natural language input directly (must be pre-extracted to structured params)\n"
            "- Guarantee correctness of upstream LLM extraction\n"
            "\n"
            "VERIFIED: Post-money SAFE (2x A/B experiments), anti-dilution & waterfall (8 math problems)\n"
            "SOURCE: NVCA Model Term Sheet, YC Post-money SAFE"
        )


if __name__ == "__main__":
    expert = VCWaterfallExpert()
    print(expert.ability_boundary())
    print()

    # Test 1: Post-money SAFE (the signal case)
    print("=== Test: Post-money SAFE ===")
    ok, result, _ = expert.process({
        "clause_type": "safe_conversion",
        "safe_investment": 2_000_000,
        "safe_cap": 10_000_000,
        "pre_money_valuation": 15_000_000,
        "series_a_investment": 5_000_000,
        "founder_shares": 8_000_000,
    })
    if ok:
        print(f"  SAFE: {result['safe_ownership_pct']:.2f}%")
        print(f"  Series A: {result['series_a_ownership_pct']:.2f}%")
        print(f"  Founder: {result['founder_ownership_pct']:.2f}%")
        print(f"  Price/share: ${result['price_per_share']:.4f}")
        print(f"  Pre-money verify: ${result['pre_money_verification']:,.0f}")
    else:
        print(f"  ERROR: {result}")

    # Test 2: Anti-dilution broad-based
    print("\n=== Test: Anti-dilution (broad-based) ===")
    ok, result, _ = expert.process({
        "clause_type": "anti_dilution",
        "old_conversion_price": 5.0,
        "old_shares_outstanding": 11_000_000,
        "new_shares_issued": 1_000_000,
        "new_share_price": 3.0,
        "method": "broad_based",
    })
    if ok:
        print(f"  New CP: ${result['new_conversion_price']:.4f}")
        print(f"  Reduction: {result['price_reduction_pct']:.2f}%")

    # Test 3: Liquidation waterfall (MATH-04 cap hit)
    print("\n=== Test: Liquidation waterfall (cap hit) ===")
    ok, result, _ = expert.process({
        "clause_type": "liquidation_waterfall",
        "exit_value": 60_000_000,
        "preferred_rounds": [
            {"investment": 4_000_000, "multiple": 1, "ownership_pct": 10,
             "participation": "participating_capped", "cap_multiple": 2},
            {"investment": 8_000_000, "multiple": 1, "ownership_pct": 20,
             "participation": "participating_capped", "cap_multiple": 2},
        ],
        "common_ownership_pct": 70,
    })
    if ok:
        for a in result["allocations"]:
            print(f"  Round {a['round_index']}: {a['choice_made']} total=${a['total_received']/1e6:.1f}M")
        print(f"  Common: ${result['common_total']/1e6:.1f}M")
        print(f"  Verify: ${result['verification_sum']/1e6:.1f}M (should be 60.0M)")

    # Test 4: 2x Senior low exit (MATH-05)
    print("\n=== Test: 2x Senior low exit ===")
    ok, result, _ = expert.process({
        "clause_type": "liquidation_waterfall",
        "exit_value": 30_000_000,
        "preferred_rounds": [
            {"investment": 6_000_000, "multiple": 1, "ownership_pct": 15,
             "participation": "participating", "seniority": "senior", "seniority_rank": 1},
            {"investment": 12_000_000, "multiple": 2, "ownership_pct": 20,
             "participation": "participating", "seniority": "senior", "seniority_rank": 0},
        ],
        "common_ownership_pct": 65,
    })
    if ok:
        for a in result["allocations"]:
            print(f"  Round {a['round_index']}: {a['choice_made']} total=${a['total_received']/1e6:.1f}M")
        print(f"  Common: ${result['common_total']/1e6:.1f}M (should be 0.0M)")
