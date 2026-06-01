import json
import logging

logger = logging.getLogger(__name__)


def run_gap_comparison(extracted_json: dict, calc_result: dict, api_key: str = "") -> dict:
    try:
        from zhipuai import ZhipuAI
        import os
        key = api_key or os.environ.get("ZHIPU_API_KEY", "")
        if not key:
            return {"error": "No API key for gap comparison"}
        client = ZhipuAI(api_key=key)
        params = {k: v for k, v in extracted_json.items() if k != "clause_type"}
        prompt = (
            "You are a general-purpose AI. Given these investment parameters, directly calculate "
            "the final ownership percentages (SAFE%, SeriesA%, Founder%). "
            "Do NOT use any tools. Give your intuitive answer.\n\n"
            f"Parameters: {json.dumps(params, ensure_ascii=False)}\n\n"
            "Return JSON: {\"safe_pct\": <number>, \"series_a_pct\": <number>, \"founder_pct\": <number>, "
            "\"reasoning\": \"<your reasoning>\"}"
        )
        resp = client.chat.completions.create(
            model="glm-5.1",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        llm_answer = json.loads(resp.choices[0].message.content)
        det_founder = calc_result.get("founder_ownership_pct", 0)
        llm_founder = llm_answer.get("founder_pct", 0)
        delta = abs(det_founder - llm_founder)
        return {
            "deterministic_result": {
                "safe_pct": calc_result.get("safe_ownership_pct"),
                "series_a_pct": calc_result.get("series_a_ownership_pct"),
                "founder_pct": det_founder,
            },
            "generic_ai_answer": llm_answer,
            "delta_pct": round(delta, 2),
            "comparison_note": "Demo only: shows deterministic engine vs generic AI difference",
        }
    except Exception as e:
        logger.warning("gap_engine.error: %s", e)
        return {"error": str(e)}
