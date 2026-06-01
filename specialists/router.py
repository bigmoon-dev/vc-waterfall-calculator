import json
import logging

logger = logging.getLogger(__name__)

KEYWORD_MAP = {
    "safe_conversion": ["safe", "simple agreement", "post-money safe", "post-money safe"],
    "anti_dilution": ["anti-dilution", "antidilution", "反稀释", "down round", "ratchet"],
    "liquidation_waterfall": ["liquidation preference", "liquidation waterfall", "清算优先", "waterfall", "exit value"],
    "participation_classify": ["participating", "non-participating", "参与型", "non participating", "participation"],
}

ROUTING_PROMPT = """You are a VC term sheet clause classifier. Given the text below, identify the primary clause type.

Return JSON with exactly these fields:
- "clause_type": one of "safe_conversion", "anti_dilution", "liquidation_waterfall", "participation_classify", or "unknown"
- "confidence": float between 0 and 1
- "detected_keywords": list of keywords that helped identify the clause type

Text:
"""


class RouterAgent:
    def route(self, raw_text: str) -> dict:
        rule_result = self._keyword_route(raw_text)
        if rule_result.get("confidence", 0) >= 0.8:
            return rule_result
        llm_result = self._llm_route(raw_text)
        if llm_result and llm_result.get("confidence", 0) > rule_result.get("confidence", 0):
            return llm_result
        return rule_result

    def _keyword_route(self, text: str) -> dict:
        text_lower = text.lower()
        best_type = "unknown"
        best_conf = 0.0
        best_kws = []
        for clause_type, kws in KEYWORD_MAP.items():
            matches = [kw for kw in kws if kw in text_lower]
            if matches and len(matches) > len(best_kws):
                best_type = clause_type
                best_kws = matches
                best_conf = min(0.5 + 0.1 * len(matches), 0.95)
        return {"clause_type": best_type, "confidence": best_conf, "detected_keywords": best_kws}

    def _llm_route(self, text: str) -> dict:
        try:
            from zhipuai import ZhipuAI
            import os
            api_key = os.environ.get("ZHIPU_API_KEY", "")
            if not api_key:
                return None
            client = ZhipuAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="glm-5.1",
                messages=[
                    {"role": "system", "content": ROUTING_PROMPT},
                    {"role": "user", "content": text[:3000]},
                ],
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.warning("router.llm_error: %s", e)
            return None
