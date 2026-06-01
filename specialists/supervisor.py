import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SupervisorAgent:
    TIMEOUT_SECONDS = 55

    def check_timeout(self, metrics: dict) -> Optional[str]:
        if not metrics:
            return None
        total = sum(m.get("duration", 0) for m in metrics.values() if isinstance(m, dict))
        if total > self.TIMEOUT_SECONDS:
            return f"Pipeline total {total:.1f}s exceeds {self.TIMEOUT_SECONDS}s threshold"
        return None

    def should_degrade_to_manual(self, error_count: int, retry_exhausted: bool) -> bool:
        return retry_exhausted or error_count >= 3

    def emit_metrics(self, metrics: dict, final_status: str):
        logger.info("pipeline.complete status=%s metrics=%s", final_status, metrics)
