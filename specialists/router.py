import json
import logging
import os

logger = logging.getLogger(__name__)

KEYWORD_MAP = {
    "safe_conversion": ["safe", "simple agreement", "post-money safe"],
    "anti_dilution": ["anti-dilution", "antidilution", "ratchet", "down round", "conversion price adjustment"],
    "liquidation_waterfall": ["liquidation preference", "liquidation waterfall", "waterfall", "exit value", "exit is", "acquired for", "sells for", "sold for", "acquisition at", "preference multiple"],
    "participation_classify": ["classify participation", "classify:", "participation type"],
}

PRIORITY_ORDER = [
    "safe_conversion",
    "anti_dilution",
    "liquidation_waterfall",
    "participation_classify",
]

ROUTING_PROMPT = """You are a VC term sheet clause classifier. Given the text below, identify the primary clause type.

IMPORTANT DISAMBIGUATION RULES:
- If the text starts with "Classify:" or "Classify participation type:", it is ALWAYS "participation_classify" regardless of other keywords.
- If the text mentions "participating preferred" or "non-participating" ALONG WITH specific financial numbers (exit value, investment amount, preference multiple), classify as "liquidation_waterfall" NOT "participation_classify".
- "participation_classify" is ONLY for short texts asking to classify the participation TYPE of a clause, without specific financial calculations.
- If the text describes an acquisition/exit scenario with multiple investor rounds, classify as "liquidation_waterfall".

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
        candidates = []
        for clause_type, kws in KEYWORD_MAP.items():
            matches = [kw for kw in kws if kw in text_lower]
            if matches:
                candidates.append({
                    "clause_type": clause_type,
                    "confidence": min(0.5 + 0.1 * len(matches), 0.95),
                    "detected_keywords": matches,
                    "match_count": len(matches),
                })
        if not candidates:
            return {"clause_type": "unknown", "confidence": 0.0, "detected_keywords": []}
        
        # Priority: specific financial keywords beat generic ones
        has_financial = any(
            kw in text_lower for kw in ["exit value", "exit is", "acquired for", "sells for",
                                         "sold for", "acquisition at", "$", "investment",
                                         "liquidation preference"]
        )
        if has_financial:
            waterfall = [c for c in candidates if c["clause_type"] == "liquidation_waterfall"]
            if waterfall:
                return waterfall[0]
        
        # Default: highest match count, then priority order
        candidates.sort(key=lambda c: (c["match_count"], -PRIORITY_ORDER.index(c["clause_type"])), reverse=True)
        best = candidates[0]
        # Boost confidence for "Classify:" prefix (unambiguous PC signal)
        if text_lower.strip().startswith("classify") and best["clause_type"] == "participation_classify":
            best["confidence"] = 0.95
        return best

    def _llm_route(self, text: str) -> dict:
        try:
            provider = os.environ.get("LLM_PROVIDER", "deepseek")
            if provider == "deepseek":
                from openai import OpenAI
                api_key = os.environ.get("DEEPSEEK_API_KEY", "")
                if not api_key:
                    return None
                client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
                model = "deepseek-v4-flash"
            else:
                from zhipuai import ZhipuAI
                api_key = os.environ.get("ZHIPU_API_KEY", "")
                if not api_key:
                    return None
                client = ZhipuAI(api_key=api_key)
                model = "glm-5.1"
            resp = client.chat.completions.create(
                model=model,
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
