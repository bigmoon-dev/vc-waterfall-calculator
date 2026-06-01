import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BLINDSPOT_SCAN_PROMPT = """You are a VC term sheet risk analyst. Given the extracted structured parameters from an investment clause, identify potential blind spots that a general AI might miss.

Check for these specific risk patterns:
1. Multiple liquidation preference stacking (multiple preferred rounds with high multiples)
2. Ambiguous participation type (participating vs non-participating unclear)
3. Missing pay-to-play provisions (no downside protection for investors)
4. Hidden redemption obligations (company forced to buy back shares)
5. Disproportionate anti-dilution protection (full ratchet in down round)
6. Cap table inconsistencies (ownership percentages don't sum to 100%)

Return JSON:
{
  "alerts": [
    {
      "title": "brief title",
      "severity": "critical" or "warning" or "info",
      "source_ref": "which field/pattern triggered this",
      "detail": "why this is a risk"
    }
  ]
}

If no risks found, return {"alerts": []}.
"""


class BlindSpotAgent:
    KNOWN_RULES = [
        {"id": "BS-001", "pattern": "multiple_liquidation_preference", "severity": "critical",
         "title": "Multiple liquidation preference stacking"},
        {"id": "BS-002", "pattern": "participation_ambiguity", "severity": "warning",
         "title": "Participation type ambiguity"},
        {"id": "BS-003", "pattern": "missing_pay_to_play", "severity": "warning",
         "title": "Missing pay-to-play provision"},
        {"id": "BS-004", "pattern": "hidden_redemption", "severity": "critical",
         "title": "Hidden redemption obligation"},
    ]

    def scan(self, extracted_json: dict, route_result: dict) -> dict:
        rule_alerts = self._rule_scan(extracted_json)
        llm_alerts = self._llm_scan(extracted_json)
        all_alerts = rule_alerts + llm_alerts
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        all_alerts.sort(key=lambda a: sev_order.get(a.get("severity", "info"), 3))
        return {"alerts": all_alerts, "total": len(all_alerts)}

    def _rule_scan(self, data: dict) -> list:
        alerts = []
        lp_count = sum(1 for k in data if "liquidation" in k.lower() or "优先" in k.lower())
        rounds = data.get("preferred_rounds", [])
        if isinstance(rounds, list) and len(rounds) > 1:
            high_mult = [r for r in rounds if isinstance(r, dict) and float(r.get("multiple", 0)) > 1]
            if high_mult:
                alerts.append({
                    "title": f"Detected {len(high_mult)} preferred round(s) with multiple > 1x",
                    "severity": "critical",
                    "source_ref": "Rule engine BS-001",
                    "detail": f"Found {len(rounds)} preferred rounds, {len(high_mult)} with multiples > 1x. Stacking risk.",
                })

        if isinstance(rounds, list):
            for r in rounds:
                if isinstance(r, dict):
                    p = r.get("participation", "")
                    if p == "participating" and r.get("cap_multiple") is None:
                        alerts.append({
                            "title": "Uncapped participating preferred detected",
                            "severity": "warning",
                            "source_ref": "Rule engine BS-002",
                            "detail": "Participating preferred without a cap means investor gets preference + full upside. This is the most investor-favorable participation structure.",
                        })
                    elif p in ("participating_capped",) and r.get("cap_multiple"):
                        pass

        ad_method = data.get("method", "")
        if ad_method == "full_ratchet":
            alerts.append({
                "title": "Full ratchet anti-dilution detected",
                "severity": "warning",
                "source_ref": "Rule engine: anti_dilution full_ratchet",
                "detail": "Full ratchet is the most investor-favorable anti-dilution. Check if this is market standard for this round.",
            })

        clause_text = data.get("clause_text", "") or ""
        keywords = data.get("clause_text_keywords", []) or []
        combined = clause_text.lower() + " " + " ".join(str(k) for k in keywords).lower()
        pay_to_play_terms = ["pay-to-play", "pay to play", "paytoplay"]
        has_ptp = any(t in combined for t in pay_to_play_terms)
        if not has_ptp and (len(rounds) > 0 if isinstance(rounds, list) else False):
            alerts.append({
                "title": "No pay-to-play provision detected",
                "severity": "warning",
                "source_ref": "Rule engine BS-003",
                "detail": "Missing pay-to-play means investors who don't participate in down-rounds keep their preferences. This protects investors at the expense of founders and participating investors.",
            })

        redemption_terms = ["redemption", "repurchase", "put right", "put option", "mandatory redemption"]
        has_redemption = any(t in combined for t in redemption_terms)
        if has_redemption:
            alerts.append({
                "title": "Redemption/repurchase obligation detected",
                "severity": "critical",
                "source_ref": "Rule engine BS-004",
                "detail": "Company may be obligated to buy back shares, typically after 5-7 years. This can create significant cash flow pressure and effectively force a liquidity event.",
            })

        return alerts

    def _llm_scan(self, data: dict) -> list:
        try:
            from zhipuai import ZhipuAI
            import os
            api_key = os.environ.get("ZHIPU_API_KEY", "")
            if not api_key:
                return []
            client = ZhipuAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="glm-5.1",
                messages=[
                    {"role": "system", "content": BLINDSPOT_SCAN_PROMPT},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)[:4000]},
                ],
                response_format={"type": "json_object"},
            )
            result = json.loads(resp.choices[0].message.content)
            return result.get("alerts", [])
        except Exception as e:
            logger.warning("blindspot.llm_error: %s", e)
            return []
