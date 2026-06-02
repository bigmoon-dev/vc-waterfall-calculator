import time
import os
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import streamlit as st

logger = logging.getLogger(__name__)


class Phase(Enum):
    IDLE = auto()
    ROUTING = auto()
    EXTRACTION = auto()
    EXTRACTION_REVIEW = auto()
    BLINDSPOT = auto()
    CALCULATION = auto()
    VALIDATION = auto()
    ERROR = auto()


@dataclass
class PhaseMetrics:
    phase_name: str
    entered_at: float = 0.0
    exited_at: float = 0.0
    retry_count: int = 0
    status: str = "pending"


class PhaseExitConditionError(Exception):
    def __init__(self, message: str, target_phase: Phase, reason: str):
        super().__init__(message)
        self.target_phase = target_phase
        self.reason = reason


FIELD_RANGES = {
    "discount_rate": (0, 1),
    "liquidation_multiple": (0.5, 10),
    "valuation_pre": (0, None),
    "valuation_post": (0, None),
    "investment_amount": (0, None),
    "participation_cap": (0, None),
    "founder_shares": (0, None),
    "safe_investment": (0, None),
    "safe_cap": (0, None),
    "pre_money_valuation": (0, None),
    "series_a_investment": (0, None),
}


def semantic_validate(raw_json: dict) -> dict:
    warnings = []
    for fld, (lo, hi) in FIELD_RANGES.items():
        val = raw_json.get(fld)
        if val is None:
            continue
        if lo is not None and val <= lo:
            warnings.append(f"{fld}={val} <= {lo}")
        if hi is not None and val >= hi:
            warnings.append(f"{fld}={val} >= {hi}")
    return {"valid": len(warnings) == 0, "warnings": warnings}


def sanitize_input(text: str, max_length: int = 50000) -> str:
    if not text:
        return ""
    text = text[:max_length]
    cleaned = []
    for ch in text:
        if ord(ch) < 32 and ch not in ('\n', '\r', '\t'):
            continue
        cleaned.append(ch)
    return ''.join(cleaned)


class PhaseStateMachine:
    MAX_RETRIES = 2
    RETRY_BACKOFF_PHASES = {Phase.EXTRACTION}

    def __init__(self):
        self.current_phase = Phase.IDLE
        self._metrics: Dict[str, PhaseMetrics] = {}
        self._retry_counts: Dict[Phase, int] = {}
        self._done = False

    def reset(self):
        self.current_phase = Phase.IDLE
        self._metrics = {}
        self._retry_counts = {}
        self._done = False

    def _record_enter(self, phase: Phase):
        name = phase.name
        if name not in self._metrics:
            self._metrics[name] = PhaseMetrics(phase_name=name)
        self._metrics[name].entered_at = time.time()
        self._metrics[name].status = "running"

    def _record_exit(self, phase: Phase, status: str = "complete"):
        name = phase.name
        if name in self._metrics:
            self._metrics[name].exited_at = time.time()
            self._metrics[name].status = status

    def _get_retry_count(self, phase: Phase) -> int:
        return self._retry_counts.get(phase, 0)

    def _increment_retry(self, phase: Phase):
        self._retry_counts[phase] = self._retry_counts.get(phase, 0) + 1
        name = phase.name
        if name in self._metrics:
            self._metrics[name].retry_count = self._retry_counts[phase]

    def _advance(self, target: Phase):
        self._record_exit(self.current_phase)
        self.current_phase = target
        self._record_enter(target)

    def _rework_to(self, target: Phase, reason: str):
        self._record_exit(self.current_phase, status=f"rework->{target.name}")
        logger.warning("phase.rework from=%s to=%s reason=%s",
                       self.current_phase.name, target.name, reason)
        self.current_phase = target
        self._record_enter(target)

    def _go_error(self, msg: str):
        self._record_exit(self.current_phase, status="error")
        st.session_state.error_msg = msg
        self.current_phase = Phase.ERROR

    def _check_exit(self, phase: Phase, conditions: Dict[str, Any]):
        for name, value in conditions.items():
            if not value:
                raise PhaseExitConditionError(
                    f"Exit Condition Failed: {name}",
                    target_phase=phase,
                    reason=name,
                )

    def execute_current_phase(self):
        ctx = st.session_state
        phase = self.current_phase

        if phase == Phase.IDLE or self._done:
            return

        try:
            if phase == Phase.ROUTING:
                self._execute_routing(ctx)
                phase = self.current_phase
            if phase == Phase.EXTRACTION:
                self._execute_extraction(ctx)
                phase = self.current_phase
            if phase == Phase.EXTRACTION_REVIEW:
                self._execute_extraction_review(ctx)
                return
            if phase == Phase.BLINDSPOT:
                self._execute_blindspot(ctx)
                phase = self.current_phase
            if phase == Phase.CALCULATION:
                self._execute_calculation(ctx)
                phase = self.current_phase
            if phase == Phase.VALIDATION:
                self._execute_validation(ctx)

        except PhaseExitConditionError as e:
            if (e.target_phase in self.RETRY_BACKOFF_PHASES
                    and self._get_retry_count(e.target_phase) < self.MAX_RETRIES):
                self._increment_retry(e.target_phase)
                self._rework_to(e.target_phase, e.reason)
            else:
                self._go_error(f"{e}. Max retries exceeded.")

        except Exception as e:
            logger.exception("phase.error phase=%s", phase.name)
            self._go_error(str(e))

    def _execute_routing(self, ctx):
        from specialists.router import RouterAgent
        router = RouterAgent()
        result = router.route(ctx.raw_text)
        ctx.route_result = result
        self._check_exit(Phase.EXTRACTION, {"route_result": result is not None})
        self._advance(Phase.EXTRACTION)

    def _execute_extraction(self, ctx):
        from specialists.extractor import ExtractorAgent
        extractor = ExtractorAgent()
        raw_json, is_valid = extractor.extract(ctx.raw_text, ctx.route_result)
        ctx.extracted_json = raw_json

        sem = semantic_validate(raw_json)
        ctx.semantic_check = sem

        self._check_exit(Phase.EXTRACTION_REVIEW, {
            "pydantic_valid": is_valid,
            "semantic_valid": sem['valid'],
        })
        ctx.raw_text = None
        self._advance(Phase.EXTRACTION_REVIEW)

    def _execute_extraction_review(self, ctx):
        pass

    def _execute_blindspot(self, ctx):
        from specialists.blindspot import BlindSpotAgent
        scanner = BlindSpotAgent()
        result = scanner.scan(ctx.extracted_json, ctx.route_result)
        ctx.blindspot_result = result
        self._check_exit(Phase.CALCULATION, {"blindspot_result": result is not None})
        self._advance(Phase.CALCULATION)

    def _execute_calculation(self, ctx):
        from experts.vc_waterfall import VCWaterfallExpert
        expert = VCWaterfallExpert()
        payload = dict(ctx.extracted_json)
        ok, result, boundary = expert.process(payload)
        if not ok:
            raise PhaseExitConditionError(
                f"Computation failed: {result}",
                target_phase=Phase.EXTRACTION,
                reason="calculation_error",
            )
        ctx.calc_result = result
        ctx.is_safe_clause = payload.get("clause_type") == "safe_conversion"
        self._check_exit(Phase.VALIDATION, {"calc_result": result is not None})
        self._advance(Phase.VALIDATION)

    def _validate_calc_result(self, calc_result: dict, extracted_json: dict) -> bool:
        if not calc_result:
            return False
        if "safe_ownership_pct" in calc_result:
            total = (calc_result.get("safe_ownership_pct", 0)
                     + calc_result.get("series_a_ownership_pct", 0)
                     + calc_result.get("founder_ownership_pct", 0))
            if abs(total - 100.0) > 0.5:
                logger.warning("validation.fail ownership_sum=%.2f", total)
                return False
            if calc_result.get("founder_ownership_pct", 0) <= 0:
                logger.warning("validation.fail founder_pct<=0")
                return False
        if "allocations" in calc_result:
            verify = calc_result.get("verification_sum", 0)
            exit_val = extracted_json.get("exit_value", 0)
            if verify > 0 and exit_val > 0 and abs(verify - exit_val) > 1.0:
                logger.warning("validation.fail waterfall_verify=%.0f exit=%.0f", verify, exit_val)
                return False
        return True

    def _execute_validation(self, ctx):
        calc_ok = self._validate_calc_result(ctx.calc_result, ctx.extracted_json)
        if not calc_ok:
            raise PhaseExitConditionError(
                "Validation failed: calc result sanity check",
                target_phase=Phase.EXTRACTION,
                reason="validation_sanity_check_failed",
            )

        ctx.validator_result = {"status": "passed", "checks": {"sanity": True}}

        from specialists.supervisor import SupervisorAgent
        supervisor = SupervisorAgent()
        pipeline_so_far = {
            name: {"duration": round(m.exited_at - m.entered_at, 2) if m.entered_at and m.exited_at else 0}
            for name, m in self._metrics.items()
        }
        timeout_msg = supervisor.check_timeout(pipeline_so_far)
        if timeout_msg:
            logger.warning("supervisor.timeout: %s", timeout_msg)
        supervisor.emit_metrics(pipeline_so_far, "completing")

        if ctx.get('is_safe_clause'):
            try:
                from gap_engine import run_gap_comparison
                api_key = os.environ.get("ZHIPU_API_KEY", "")
                ctx.gap_data = run_gap_comparison(
                    ctx.extracted_json, ctx.calc_result, api_key,
                )
            except Exception as e:
                logger.warning("gap_engine.error: %s", e)
                ctx.gap_data = None

        ctx.pipeline_metrics = {
            name: {
                "duration": round(m.exited_at - m.entered_at, 2) if m.entered_at and m.exited_at else 0,
                "retries": m.retry_count,
                "status": m.status,
            }
            for name, m in self._metrics.items()
            if m.entered_at and m.exited_at
        }
        self._record_exit(Phase.VALIDATION)
        self.current_phase = Phase.VALIDATION
        self._done = True

    def get_metrics_summary(self) -> dict:
        return {
            name: {
                "duration": round(m.exited_at - m.entered_at, 2) if m.entered_at and m.exited_at else 0,
                "retries": m.retry_count,
                "status": m.status,
            }
            for name, m in self._metrics.items()
        }
