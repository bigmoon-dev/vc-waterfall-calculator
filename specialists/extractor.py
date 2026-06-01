import json
import logging
from typing import Tuple

from pydantic import BaseModel, ValidationError

from experts.vc_waterfall import (
    SAFEConversionInput,
    AntiDilutionInput,
    LiquidationWaterfallInput,
    ParticipationClassifyInput,
)
from extractor_prompts import EXTRACTOR_MAP

logger = logging.getLogger(__name__)

SCHEMA_MAP = {
    "safe_conversion": SAFEConversionInput,
    "anti_dilution": AntiDilutionInput,
    "liquidation_waterfall": LiquidationWaterfallInput,
    "participation_classify": ParticipationClassifyInput,
    "default": SAFEConversionInput,
}

PROMPT_MAP = EXTRACTOR_MAP


class ExtractorAgent:
    MAX_RETRIES = 2

    def extract(self, raw_text: str, route_result: dict) -> Tuple[dict, bool]:
        clause_type = route_result.get("clause_type", "unknown")
        schema_cls = SCHEMA_MAP.get(clause_type, SCHEMA_MAP["default"])
        prompt = PROMPT_MAP.get(clause_type, PROMPT_MAP.get("safe_conversion", ""))

        last_error = []
        for attempt in range(self.MAX_RETRIES + 1):
            raw_json = self._call_llm(prompt, raw_text)
            if raw_json is None:
                continue
            is_valid, errors = self._pydantic_validate(raw_json, schema_cls)
            if is_valid:
                return raw_json, True
            last_error = errors
            prompt = self._refine_prompt(prompt, errors, attempt)

        return raw_json if raw_json else {}, False

    def _call_llm(self, system_prompt: str, user_text: str) -> dict:
        try:
            from zhipuai import ZhipuAI
            import os
            api_key = os.environ.get("ZHIPU_API_KEY", "")
            if not api_key:
                logger.error("extractor.no_api_key")
                return None
            client = ZhipuAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="glm-5.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text[:8000]},
                ],
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.warning("extractor.llm_error: %s", e)
            return None

    def _pydantic_validate(self, data: dict, schema_cls: type) -> Tuple[bool, list]:
        try:
            schema_cls(**data)
            return True, []
        except Exception as e:
            return False, str(e).split("\n")

    def _refine_prompt(self, prompt: str, errors: list, attempt: int) -> str:
        if attempt == 0:
            return prompt + "\n\nWARNING: Previous output had JSON structure errors. Output STRICTLY matching the schema."
        return prompt + "\n\nFINAL RETRY. Mandatory field types:\n" + "\n".join(errors[:5])
