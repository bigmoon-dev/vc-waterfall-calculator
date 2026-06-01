import json
import logging
import os
from typing import Tuple, Optional

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

DEFAULT_LLM_CONFIG = {
    "provider": os.environ.get("LLM_PROVIDER", "deepseek"),
    "model": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
}


class ExtractorAgent:
    MAX_RETRIES = 2

    def __init__(self, llm_config: Optional[dict] = None):
        self._llm_config = llm_config or DEFAULT_LLM_CONFIG

    def extract(self, raw_text: str, route_result: dict) -> Tuple[dict, bool]:
        clause_type = route_result.get("clause_type", "unknown")
        schema_cls = SCHEMA_MAP.get(clause_type, SCHEMA_MAP["default"])
        prompt = PROMPT_MAP.get(clause_type, PROMPT_MAP.get("safe_conversion", ""))

        last_error = []
        raw_json = None
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

    def _call_llm(self, system_prompt: str, user_text: str) -> Optional[dict]:
        provider = self._llm_config["provider"]
        model = self._llm_config["model"]
        try:
            if provider == "deepseek":
                from openai import OpenAI
                api_key = os.environ.get("DEEPSEEK_API_KEY", "")
                if not api_key:
                    logger.error("extractor.no_api_key provider=deepseek")
                    return None
                client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text[:8000]},
                    ],
                    response_format={"type": "json_object"},
                )
                return json.loads(resp.choices[0].message.content)
            elif provider == "zhipu":
                from zhipuai import ZhipuAI
                api_key = os.environ.get("ZHIPU_API_KEY", "")
                if not api_key:
                    logger.error("extractor.no_api_key provider=zhipu")
                    return None
                client = ZhipuAI(api_key=api_key)
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text[:8000]},
                    ],
                    response_format={"type": "json_object"},
                )
                return json.loads(resp.choices[0].message.content)
            else:
                logger.error("extractor.unknown_provider: %s", provider)
                return None
        except Exception as e:
            logger.warning("extractor.llm_error provider=%s model=%s: %s", provider, model, e)
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
