import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from experts.vc_waterfall import VCWaterfallExpert
from phases import PhaseStateMachine, Phase, sanitize_input

expert = VCWaterfallExpert()

st.set_page_config(page_title="VC Waterfall Expert", layout="wide", page_icon="💧")


def get_api_key():
    try:
        if "ZHIPU_API_KEY" in st.secrets:
            return st.secrets["ZHIPU_API_KEY"]
    except Exception:
        pass
    env_key = os.environ.get("ZHIPU_API_KEY", "")
    if env_key:
        return env_key
    if "fallback_key" in st.session_state and st.session_state.fallback_key:
        return st.session_state.fallback_key
    return None


def _state_for(current: Phase, target: Phase) -> str:
    if current == target:
        return "running"
    if current.value > target.value:
        return "complete"
    return "complete" if current == Phase.ERROR else "running"


def _metric_line(metrics: dict, phase_name: str):
    if phase_name in metrics:
        m = metrics[phase_name]
        dur = m.get("duration", 0)
        retries = m.get("retries", 0)
        if dur > 0:
            st.caption(f"{dur:.1f}s" + (f" ({retries} retries)" if retries else ""))


REVIEW_FIELDS = {
    "safe_conversion": [
        ("safe_investment", "SAFE Investment ($)", "number"),
        ("safe_cap", "SAFE Valuation Cap ($)", "number"),
        ("pre_money_valuation", "Series A Pre-money ($)", "number"),
        ("series_a_investment", "Series A Investment ($)", "number"),
        ("founder_shares", "Founder Shares", "number"),
        ("discount_rate", "Discount Rate (0-1, 0=none)", "number"),
    ],
    "anti_dilution": [
        ("old_conversion_price", "Original Conversion Price ($)", "number"),
        ("old_shares_outstanding", "Shares Outstanding Before", "number"),
        ("new_shares_issued", "New Shares Issued", "number"),
        ("new_share_price", "New Share Price ($)", "number"),
        ("method", "Method", "text"),
    ],
    "liquidation_waterfall": [
        ("exit_value", "Exit Value ($)", "number"),
        ("common_ownership_pct", "Common Ownership %", "number"),
    ],
    "participation_classify": [
        ("clause_text_keywords", "Keywords", "text"),
    ],
}


def _render_confidence_badge(level: str):
    if level == "GREEN":
        st.success("GREEN: Pure deterministic calculation — no AI hallucination risk")
    elif level == "YELLOW":
        st.warning("YELLOW: Parameters extracted by AI + deterministic calculation — please verify inputs above")
    elif level == "RED":
        st.error("RED: Unable to process — please check input format or use Manual Mode")


def render_agent_pipeline(machine: PhaseStateMachine):
    phase = machine.current_phase
    ctx = st.session_state
    metrics = ctx.get("pipeline_metrics", {})

    with st.status("1/7 Intent Routing",
                   expanded=phase == Phase.ROUTING,
                   state=_state_for(phase, Phase.ROUTING)) as s1:
        if phase.value > Phase.ROUTING.value:
            r = ctx.get('route_result', {})
            st.write(f"**Clause type**: {r.get('clause_type', 'unknown')} "
                     f"(confidence: {r.get('confidence', 0):.0%})")
            _metric_line(metrics, "ROUTING")
            s1.update(label="1/7 Routing done", state="complete")

    with st.status("2/7 Structured Extraction",
                   expanded=phase == Phase.EXTRACTION,
                   state=_state_for(phase, Phase.EXTRACTION)) as s2:
        if phase.value > Phase.EXTRACTION.value:
            st.json(ctx.get('extracted_json', {}))
            sem = ctx.get('semantic_check')
            if sem and sem['valid']:
                st.write("Structure + semantic validation passed")
            elif sem:
                st.warning(f"Semantic range warning: {sem['warnings']}")
            _metric_line(metrics, "EXTRACTION")
            s2.update(label="2/7 Extraction done", state="complete")

    with st.status("3/7 Parameter Review",
                   expanded=phase == Phase.EXTRACTION_REVIEW,
                   state=_state_for(phase, Phase.EXTRACTION_REVIEW)) as s3:
        if phase == Phase.EXTRACTION_REVIEW:
            st.warning("AI extracted these parameters. Please verify before proceeding.")
            extracted = ctx.get('extracted_json', {})
            clause_type = extracted.get('clause_type',
                                       ctx.get('route_result', {}).get('clause_type', ''))
            fields = REVIEW_FIELDS.get(clause_type, [])
            edited = {}
            with st.form("extraction_review_form"):
                for key, label, ftype in fields:
                    default = extracted.get(key, "")
                    if ftype == "number":
                        val = st.number_input(
                            label,
                            value=float(default) if default not in (None, "") else 0.0,
                            format="%.4f",
                        )
                    else:
                        val = st.text_input(
                            label,
                            value=str(default) if default not in (None, "") else "",
                        )
                    edited[key] = val
                confirmed = st.form_submit_button("Confirm and Continue")
                if confirmed:
                    edited["clause_type"] = clause_type
                    ctx.extracted_json = edited
                    ctx.confidence_level = "GREEN"
                    machine._record_exit(Phase.EXTRACTION_REVIEW)
                    machine.current_phase = Phase.BLINDSPOT
                    machine._record_enter(Phase.BLINDSPOT)
                    st.rerun()
        elif phase.value > Phase.EXTRACTION_REVIEW.value:
            st.json(ctx.get('extracted_json', {}))
            st.success("Parameters confirmed by user")
            _metric_line(metrics, "EXTRACTION_REVIEW")
            s3.update(label="3/7 Parameter review done", state="complete")

    with st.status("4/7 Blind Spot Scan",
                   expanded=phase == Phase.BLINDSPOT,
                   state=_state_for(phase, Phase.BLINDSPOT)) as s4:
        if phase.value > Phase.BLINDSPOT.value:
            bs = ctx.get('blindspot_result', {})
            alerts = bs.get('alerts', [])
            if alerts:
                for a in alerts:
                    sev = a.get('severity', 'info')
                    icon = {'critical': '🔴', 'warning': '🟡', 'info': 'ℹ️'}.get(sev, 'ℹ️')
                    st.write(f"{icon} **[{sev.upper()}]** {a['title']}")
                    st.caption(f"Source: {a.get('source_ref', '')} | {a.get('detail', '')}")
            else:
                st.write("No known blind spot patterns detected")
            _metric_line(metrics, "BLINDSPOT")
            s4.update(label="4/7 Blind spot scan done", state="complete")

    with st.status("5/7 Deterministic Calculation",
                   expanded=phase == Phase.CALCULATION,
                   state=_state_for(phase, Phase.CALCULATION)) as s5:
        if phase.value > Phase.CALCULATION.value:
            st.write("**Engine**: Pure Python algebra (zero LLM calls)")
            result = ctx.get('calc_result', {})
            if result:
                if 'safe_ownership_pct' in result:
                    c1, c2, c3 = st.columns(3)
                    c1.metric("SAFE", f"{result['safe_ownership_pct']:.2f}%")
                    c2.metric("Series A", f"{result['series_a_ownership_pct']:.2f}%")
                    c3.metric("Founder", f"{result['founder_ownership_pct']:.2f}%")
                    st.caption(f"Total shares: {result['total_shares']:,.0f} | "
                               f"Price/share: ${result['price_per_share']:.4f} | "
                               f"Pre-money verify: ${result['pre_money_verification']:,.0f}")
                elif 'new_conversion_price' in result:
                    c1, c2 = st.columns(2)
                    c1.metric("New Conversion Price", f"${result['new_conversion_price']:.4f}")
                    c2.metric("Price Reduction", f"{result['price_reduction_pct']:.2f}%")
                elif 'allocations' in result:
                    for a in result['allocations']:
                        st.write(f"**Round {a['round_index']}**: {a['choice_made']} — "
                                 f"${a['total_received']:,.0f}")
                    c1, c2 = st.columns(2)
                    c1.metric("Common", f"${result['common_total']:,.0f}")
                    c2.metric("Verify", f"${result['verification_sum']:,.0f}")
                elif 'classification' in result:
                    c1, c2 = st.columns(2)
                    c1.metric("Classification", result["classification"])
                    c2.metric("Confidence", f"{result['confidence']:.0f}%")

            derivation_steps = result.get("derivation_steps", []) if result else []
            if derivation_steps:
                with st.expander("Calculation Proof (Step-by-step)"):
                    for i, step in enumerate(derivation_steps, 1):
                        st.markdown(f"**Step {i}: {step.get('step', '')}**")
                        st.code(step.get('formula', ''), language="text")
                        st.caption(f"Values: {step.get('values', '')}")
                        st.info(f"Result: {step.get('result', '')}")

                    from export_utils import generate_proof_excel
                    excel_bytes = generate_proof_excel(
                        ctx.get('calc_result', {}),
                        ctx.get('extracted_json', {}),
                        ctx.get('route_result', {}).get('clause_type', ''),
                    )
                    st.download_button(
                        "Export to Excel",
                        data=excel_bytes,
                        file_name="calculation_proof.xlsx",
                        mime="application/vnd.openxmlformats.officedocument.spreadsheetml.sheet",
                    )

            _metric_line(metrics, "CALCULATION")
            s5.update(label="5/7 Calculation done", state="complete")

    with st.status("6/7 Validation & Report",
                   expanded=phase == Phase.VALIDATION,
                   state=_state_for(phase, Phase.VALIDATION)) as s6:
        if phase.value >= Phase.VALIDATION.value and phase != Phase.ROUTING:
            confidence = ctx.get('confidence_level', 'YELLOW')
            if phase == Phase.ERROR:
                confidence = 'RED'
            _render_confidence_badge(confidence)

            st.write(ctx.get('validator_result', {}))
            gap = ctx.get('gap_data')
            if gap and not gap.get('error'):
                st.divider()
                st.write("**Demo: Deterministic vs Generic AI Gap Analysis**")
                c1, c2 = st.columns(2)
                with c1:
                    st.metric("Deterministic Founder%",
                              f"{gap['deterministic_result']['founder_pct']:.1f}%")
                with c2:
                    ga = gap.get('generic_ai_answer', {})
                    st.metric("Generic AI Founder%",
                              f"{ga.get('founder_pct', 'N/A')}")
                delta = gap.get('delta_pct', 0)
                if delta > 0.5:
                    st.warning(f"Delta: {delta:.1f}% = significant difference")
                else:
                    st.success(f"Delta: {delta:.1f}% = aligned")
            _metric_line(metrics, "VALIDATION")
            s6.update(label="6/7 Validation done", state="complete")

    if phase == Phase.ERROR:
        _render_confidence_badge("RED")
        st.error(f"Pipeline error: {ctx.get('error_msg', 'Unknown error')}")
        st.info("Try: check clause text format, or switch to Manual Mode")
        if st.button("Restart"):
            machine.reset()
            st.rerun()


def render_manual_calculator():
    tab_safe, tab_ad, tab_wf, tab_pc, tab_b1b2, tab_demo = st.tabs([
        "SAFE Conversion", "Anti-Dilution", "Liquidation Waterfall",
        "Participation Classify", "Price / Ownership", "Gap Demo",
    ])

    with tab_safe:
        st.header("Post-money SAFE Conversion")
        st.caption("Source: YC Post-money SAFE 2018 | Engine: DeterministicSubtaskExpert + Python Decimal")
        with st.form("safe_form"):
            c1, c2 = st.columns(2)
            with c1:
                safe_inv = st.number_input("SAFE Investment ($)", value=2_000_000, step=100_000, format="%d")
                safe_cap = st.number_input("SAFE Valuation Cap ($)", value=10_000_000, step=500_000, format="%d")
            with c2:
                pre_money = st.number_input("Series A Pre-money ($)", value=15_000_000, step=500_000, format="%d")
                sa_inv = st.number_input("Series A Investment ($)", value=5_000_000, step=500_000, format="%d")
            c3, c4 = st.columns(2)
            with c3:
                founder_shares = st.number_input("Founder Shares", value=8_000_000, step=100_000, format="%d")
            with c4:
                discount_input = st.number_input("Discount Rate % (0 = none)", value=0.0, min_value=0.0, max_value=99.0, step=5.0, format="%.0f")
            submitted = st.form_submit_button("Calculate")
            if submitted:
                payload = {
                    "clause_type": "safe_conversion",
                    "safe_investment": safe_inv,
                    "safe_cap": safe_cap,
                    "pre_money_valuation": pre_money,
                    "series_a_investment": sa_inv,
                    "founder_shares": founder_shares,
                }
                if discount_input > 0:
                    payload["discount_rate"] = discount_input / 100.0
                ok, result, boundary = expert.process(payload)
                if ok:
                    mechanism = result["effective_mechanism"]
                    mech_labels = {
                        "cap_only": "Valuation Cap Only",
                        "cap": "Valuation Cap (lower)",
                        "discount": "Discount Rate (lower)",
                    }
                    st.info(f"**Effective Mechanism:** {mech_labels.get(mechanism, mechanism)}")
                    if result.get("cap_price_per_share") and result.get("discount_price_per_share"):
                        pc1, pc2 = st.columns(2)
                        cap_h = " **<- binding**" if mechanism in ("cap_only", "cap") else ""
                        disc_h = " **<- binding**" if mechanism == "discount" else ""
                        pc1.metric("Cap Price/Share", f"${result['cap_price_per_share']:.4f}",
                                   delta="binding" if mechanism in ("cap_only", "cap") else None)
                        pc2.metric("Discount Price/Share", f"${result['discount_price_per_share']:.4f}",
                                   delta="binding" if mechanism == "discount" else None)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("SAFE Ownership", f"{result['safe_ownership_pct']:.2f}%")
                    c2.metric("Series A Ownership", f"{result['series_a_ownership_pct']:.2f}%")
                    c3.metric("Founder Ownership", f"{result['founder_ownership_pct']:.2f}%")
                    st.markdown(f"**Total Shares:** {result['total_shares']:,.0f} | **Price/Share:** ${result['price_per_share']:.4f} | **Pre-money Verify:** ${result['pre_money_verification']:,.0f}")
                    with st.expander("Derivation"):
                        derivation_steps = result.get("derivation_steps", [])
                        if derivation_steps:
                            for i, step in enumerate(derivation_steps, 1):
                                st.markdown(f"**Step {i}: {step.get('step', '')}**")
                                st.code(step.get('formula', ''), language="text")
                                st.caption(f"Values: {step.get('values', '')}")
                                st.info(f"Result: {step.get('result', '')}")
                        else:
                            deriv = f"SAFE% (cap) = ${safe_inv:,.0f} / ${safe_cap:,.0f} = {safe_inv/safe_cap*100:.2f}%\n"
                            deriv += f"SeriesA% = ${sa_inv:,.0f} / (${pre_money:,.0f} + ${sa_inv:,.0f}) = {result['series_a_ownership_pct']:.2f}%\n"
                            if discount_input > 0 and result.get("discount_price_per_share"):
                                deriv += f"\nCap Price/Share = ${result['cap_price_per_share']:.4f}\n"
                                deriv += f"Discount Price/Share = SeriesA Price * (1 - {discount_input:.0f}%) = ${result['discount_price_per_share']:.4f}\n"
                                deriv += f"Effective = min(cap, discount) = ${result['cap_price_per_share'] if mechanism in ('cap_only','cap') else result['discount_price_per_share']:.4f}\n\n"
                            deriv += f"Founder% = {result['founder_ownership_pct']:.2f}%\n"
                            deriv += f"Total Shares = {founder_shares:,} / {result['founder_ownership_pct']/100:.6f} = {result['total_shares']:,.0f}\n"
                            deriv += f"Price/Share = ${sa_inv:,.0f} / {result['series_a_shares']:,.0f} = ${result['price_per_share']:.4f}\n"
                            deriv += f"Pre-money Verify = ${result['pre_money_verification']:,.0f}"
                            st.code(deriv, language="python")
                else:
                    st.error(f"Computation failed: {result}")

    with tab_ad:
        st.header("Anti-Dilution Adjustment")
        st.caption("Source: NVCA Model Term Sheet v3.0 | Methods: Broad-based / Narrow-based / Full Ratchet")
        with st.form("ad_form"):
            c1, c2 = st.columns(2)
            with c1:
                old_cp = st.number_input("Original Conversion Price ($)", value=5.00, step=0.10, format="%.2f")
                old_shares = st.number_input("Shares Outstanding Before", value=11_000_000, step=100_000, format="%d")
            with c2:
                new_price = st.number_input("New Share Price (Down-round) ($)", value=3.00, step=0.10, format="%.2f")
                new_shares = st.number_input("New Shares Issued", value=1_000_000, step=100_000, format="%d")
            method = st.selectbox("Method", ["broad_based", "narrow_based", "full_ratchet"],
                                  format_func=lambda x: {"broad_based": "Broad-based Weighted Average",
                                                         "narrow_based": "Narrow-based Weighted Average",
                                                         "full_ratchet": "Full Ratchet"}[x])
            submitted = st.form_submit_button("Calculate")
            if submitted:
                ok, result, boundary = expert.process({
                    "clause_type": "anti_dilution",
                    "old_conversion_price": old_cp,
                    "old_shares_outstanding": old_shares,
                    "new_shares_issued": new_shares,
                    "new_share_price": new_price,
                    "method": method,
                })
                if ok:
                    c1, c2 = st.columns(2)
                    c1.metric("New Conversion Price", f"${result['new_conversion_price']:.4f}")
                    c2.metric("Price Reduction", f"{result['price_reduction_pct']:.2f}%")
                    if result.get("formula_steps") or result.get("derivation_steps"):
                        with st.expander("Derivation"):
                            derivation_steps = result.get("derivation_steps", [])
                            if derivation_steps:
                                for i, step in enumerate(derivation_steps, 1):
                                    st.markdown(f"**Step {i}: {step.get('step', '')}**")
                                    st.code(step.get('formula', ''), language="text")
                                    st.caption(f"Values: {step.get('values', '')}")
                                    st.info(f"Result: {step.get('result', '')}")
                            for step in result.get("formula_steps", []):
                                st.markdown(step)
                else:
                    st.error(f"Computation failed: {result}")

    with tab_wf:
        st.header("Liquidation Waterfall")
        st.caption("Two-pass architecture: Preference -> Participation")
        with st.form("wf_form"):
            exit_val = st.number_input("Exit Value ($)", value=60_000_000, step=1_000_000, format="%d")
            common_pct = st.number_input("Common Ownership %", value=70.0, step=1.0, format="%.1f")
            st.markdown("**Preferred Rounds** (enter 1-3 rounds)")
            rounds_data = []
            for i in range(3):
                with st.expander(f"Round {i+1}", expanded=(i < 2)):
                    inv = st.number_input(f"R{i+1} Investment ($)", value=[4_000_000, 8_000_000, 5_000_000][i], key=f"wf_inv_{i}", format="%d")
                    mult = st.number_input(f"R{i+1} Preference Multiple", value=1.0, key=f"wf_mult_{i}", format="%.1f")
                    own = st.number_input(f"R{i+1} Ownership %", value=[10.0, 20.0, 15.0][i], key=f"wf_own_{i}", format="%.1f")
                    part = st.selectbox(f"R{i+1} Participation", ["non_participating", "participating", "participating_capped"], index=2 if i < 2 else 0, key=f"wf_part_{i}")
                    cap_mult = st.number_input(f"R{i+1} Cap Multiple", value=2.0 if i < 2 else 0.0, key=f"wf_cap_{i}", format="%.1f")
                    enabled = st.checkbox(f"Include Round {i+1}", value=(i < 2), key=f"wf_en_{i}")
                    if enabled:
                        r = {"investment": inv, "multiple": mult, "ownership_pct": own, "participation": part}
                        if part == "participating_capped" and cap_mult > 0:
                            r["cap_multiple"] = cap_mult
                        rounds_data.append(r)
            submitted = st.form_submit_button("Run Waterfall")
            if submitted and rounds_data:
                ok, result, boundary = expert.process({
                    "clause_type": "liquidation_waterfall",
                    "exit_value": exit_val,
                    "preferred_rounds": rounds_data,
                    "common_ownership_pct": common_pct,
                })
                if ok:
                    for a in result["allocations"]:
                        st.markdown(f"**Round {a['round_index']}**: {a['choice_made']} — Preference ${a['preference_amount']:,.0f} + Participation ${a['participation_amount']:,.0f} = **${a['total_received']:,.0f}**")
                    c1, c2 = st.columns(2)
                    c1.metric("Common Payout", f"${result['common_total']:,.0f}")
                    c2.metric("Verification Sum", f"${result['verification_sum']:,.0f} (should = ${exit_val:,.0f})")
                else:
                    st.error(f"Computation failed: {result}")

    with tab_pc:
        st.header("Participation Type Classification")
        st.caption("Deterministic keyword-based classifier")
        with st.form("pc_form"):
            keywords = st.text_input("Clause Keywords (comma-separated)", value="participating, capped, 3x")
            submitted = st.form_submit_button("Classify")
            if submitted:
                ok, result, boundary = expert.process({
                    "clause_type": "participation_classify",
                    "clause_text_keywords": [k.strip() for k in keywords.split(",")],
                })
                if ok:
                    c1, c2 = st.columns(2)
                    c1.metric("Classification", result["classification"])
                    c2.metric("Confidence", f"{result['confidence']:.0f}%")
                    st.info(result["reasoning"])
                else:
                    st.error(f"Classification failed: {result}")

    with tab_b1b2:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("B1: Price Per Share")
            with st.form("b1_form"):
                pre_val = st.number_input("Pre-money Valuation ($)", value=15_000_000, format="%d")
                fd_shares = st.number_input("Fully Diluted Shares (post-money)", value=14_545_455, format="%d")
                submitted = st.form_submit_button("Calculate PPS")
                if submitted:
                    pps = pre_val / fd_shares
                    st.metric("Price Per Share", f"${pps:.4f}")
                    st.code(f"PPS = Pre-money / Fully Diluted Shares\n      = ${pre_val:,.0f} / {fd_shares:,.0f} = ${pps:.4f}")
        with c2:
            st.subheader("B2: Ownership %")
            with st.form("b2_form"):
                shares_owned = st.number_input("Shares Owned", value=8_000_000, format="%d")
                total_shares = st.number_input("Total Fully Diluted Shares", value=14_545_455, format="%d")
                submitted = st.form_submit_button("Calculate Ownership")
                if submitted:
                    own_pct = shares_owned / total_shares * 100
                    st.metric("Ownership", f"{own_pct:.2f}%")
                    st.code(f"Ownership = Shares Owned / Total Shares * 100\n           = {shares_owned:,} / {total_shares:,} * 100 = {own_pct:.2f}%")

    with tab_demo:
        st.header("Post-money SAFE: General AI vs Deterministic Engine")
        st.markdown("""
**The only reproducible structural blind spot** found in 2 independent A/B experiments.

**Scenario:** $2M SAFE at $10M cap, Series A at $15M pre-money investing $5M, 8M founder shares.
""")
        if st.button("Run Comparison", type="primary"):
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("General AI (Pre-money logic)")
                st.code(
                    "SAFE post-money -> 20% x (1 - SeriesA%)\n"
                    "                 = 20% x (1 - 25%)\n"
                    "                 = 15%  <- Pre-money SAFE logic!\n\n"
                    "Founder = (1 - 15% - 25%) x 8M / (8M + 2M + 5M)\n"
                    "        = 60%",
                    language="python"
                )
                st.error("**Founder = 60%** — WRONG by 5 percentage points")
            with c2:
                st.subheader("Deterministic Engine (Post-money)")
                ok, result, _ = expert.process({
                    "clause_type": "safe_conversion",
                    "safe_investment": 2_000_000,
                    "safe_cap": 10_000_000,
                    "pre_money_valuation": 15_000_000,
                    "series_a_investment": 5_000_000,
                    "founder_shares": 8_000_000,
                })
                st.code(
                    f"SAFE% = $2M / $10M = {result['safe_ownership_pct']:.1f}%  (fixed, not diluted)\n"
                    f"SeriesA% = $5M / ($15M + $5M) = {result['series_a_ownership_pct']:.1f}%\n"
                    f"Founder% = 100% - {result['safe_ownership_pct']:.1f}% - {result['series_a_ownership_pct']:.1f}% = {result['founder_ownership_pct']:.1f}%\n\n"
                    f"Price/Share = ${result['price_per_share']:.4f}\n"
                    f"Pre-money Verify = ${result['pre_money_verification']:,.0f}",
                    language="python"
                )
                st.success(f"**Founder = {result['founder_ownership_pct']:.1f}%** — CORRECT")

            st.markdown("---")
            exit_val = 60_000_000
            gap_pct = 0.60 - result['founder_ownership_pct'] / 100
            gap_dollars = exit_val * gap_pct
            st.warning(f"**Economic impact at ${exit_val/1e6:.0f}M exit:** General AI overstates founder's position by **{gap_pct*100:.1f}%** = **${gap_dollars/1e6:.1f}M** difference.")


# --- Main Layout ---

with st.sidebar:
    st.title("Agent Config")
    api_key = get_api_key()
    if api_key:
        st.success("LLM API Key ready")
        os.environ["ZHIPU_API_KEY"] = api_key
    else:
        st.error("No API Key detected. Agent mode unavailable.")
        st.text_input("Enter Key manually", type="password", key="fallback_key")

    mode_switch = st.radio("Mode", ["Agent Mode", "Manual Mode"])

if mode_switch == "Manual Mode":
    render_manual_calculator()
else:
    tab_agent, tab_glossary = st.tabs(["Agent Analysis", "Glossary"])

    with tab_agent:
        if "phase_machine" not in st.session_state:
            st.session_state.phase_machine = PhaseStateMachine()

        machine = st.session_state.phase_machine

        user_input = st.text_area(
            "Paste investment clause text",
            height=200,
            placeholder="e.g., $2M SAFE at $10M cap, Series A at $15M pre-money investing $5M...",
        )

        if st.button("Start Agent Analysis", type="primary", disabled=not api_key):
            st.session_state.raw_text = sanitize_input(user_input)
            machine.reset()
            machine.current_phase = Phase.ROUTING
            st.rerun()

        if machine.current_phase != Phase.IDLE:
            machine.execute_current_phase()
            render_agent_pipeline(machine)

    with tab_glossary:
        st.header("VC Term Glossary")
        glossary = {
            "Post-money SAFE": "Simple Agreement for Future Equity. Investor gets equity at a future priced round, with valuation cap and optional discount.",
            "Valuation Cap": "Maximum company valuation at which SAFE converts. Lower cap = more shares for SAFE investor.",
            "Discount Rate": "Percentage discount SAFE investor gets on the priced round price. e.g., 20% discount means SAFE converts at 80% of Series A price.",
            "Liquidation Preference": "In an exit (acquisition/IPO), preferred shareholders get paid before common shareholders. Usually 1x investment amount.",
            "Participating Preferred": "Investor gets liquidation preference AND participates in remaining proceeds pro-rata. Also called 'double-dip'.",
            "Non-Participating Preferred": "Investor chooses between liquidation preference or converting to common and sharing pro-rata. More founder-friendly.",
            "Anti-Dilution": "Protection for investors in a down-round. Adjusts conversion price to compensate for lower new share price.",
            "Full Ratchet": "Most investor-favorable anti-dilution: conversion price drops to the new (lower) share price directly.",
            "Broad-based Weighted Average": "Standard anti-dilution method: considers both old and new shares, adjusts price proportionally.",
        }
        for term, definition in glossary.items():
            st.markdown(f"**{term}**: {definition}")
