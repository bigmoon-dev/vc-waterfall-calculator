import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from experts.vc_waterfall import VCWaterfallExpert
from phases import PhaseStateMachine, Phase, sanitize_input
from i18n import t, lang_names, TRANSLATIONS

expert = VCWaterfallExpert()

st.set_page_config(page_title="VC Waterfall Expert", layout="wide", page_icon="💧")


def get_api_key():
    try:
        if "DEEPSEEK_API_KEY" in st.secrets:
            os.environ["DEEPSEEK_API_KEY"] = st.secrets["DEEPSEEK_API_KEY"]
            os.environ.setdefault("LLM_PROVIDER", "deepseek")
            return st.secrets["DEEPSEEK_API_KEY"]
        if "ZHIPU_API_KEY" in st.secrets:
            os.environ["ZHIPU_API_KEY"] = st.secrets["ZHIPU_API_KEY"]
            os.environ.setdefault("LLM_PROVIDER", "zhipu")
            return st.secrets["ZHIPU_API_KEY"]
    except Exception:
        pass
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    if os.environ.get("ZHIPU_API_KEY"):
        return os.environ["ZHIPU_API_KEY"]
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
            st.caption(f"{dur:.1f}s" + (f" ({retries} {t('retries')})" if retries else ""))


REVIEW_FIELDS = {
    "safe_conversion": [
        ("safe_investment", "safe_investment", "number"),
        ("safe_cap", "safe_cap", "number"),
        ("pre_money_valuation", "pre_money", "number"),
        ("series_a_investment", "sa_investment", "number"),
        ("founder_shares", "founder_shares", "number"),
        ("discount_rate", "discount_rate", "number"),
    ],
    "anti_dilution": [
        ("old_conversion_price", "old_conversion_price", "number"),
        ("old_shares_outstanding", "shares_outstanding", "number"),
        ("new_shares_issued", "new_shares_issued", "number"),
        ("new_share_price", "new_share_price", "number"),
        ("method", "method", "text"),
    ],
    "liquidation_waterfall": [
        ("exit_value", "exit_value", "number"),
        ("common_ownership_pct", "common_ownership", "number"),
    ],
    "participation_classify": [
        ("clause_text_keywords", "keywords", "text"),
    ],
}


def _render_confidence_badge(level: str):
    if level == "GREEN":
        st.success(t("green_badge"))
    elif level == "YELLOW":
        st.warning(t("yellow_badge"))
    elif level == "RED":
        st.error(t("red_badge"))


def render_agent_pipeline(machine: PhaseStateMachine):
    phase = machine.current_phase
    ctx = st.session_state
    metrics = ctx.get("pipeline_metrics", {})

    with st.status(t("routing_step"),
                   expanded=phase == Phase.ROUTING,
                   state=_state_for(phase, Phase.ROUTING)) as s1:
        if phase.value > Phase.ROUTING.value:
            r = ctx.get('route_result', {})
            st.write(f"{t('clause_type')}: {r.get('clause_type', 'unknown')} "
                     f"({t('confidence_label')}: {r.get('confidence', 0):.0%})")
            _metric_line(metrics, "ROUTING")
            s1.update(label=t("routing_done"), state="complete")

    with st.status(t("extraction_step"),
                   expanded=phase == Phase.EXTRACTION,
                   state=_state_for(phase, Phase.EXTRACTION)) as s2:
        if phase.value > Phase.EXTRACTION.value:
            st.json(ctx.get('extracted_json', {}))
            sem = ctx.get('semantic_check')
            if sem and sem['valid']:
                st.write(t("struct_semantic_pass"))
            elif sem:
                st.warning(f"{t('semantic_range_warn')}: {sem['warnings']}")
            _metric_line(metrics, "EXTRACTION")
            s2.update(label=t("extraction_done"), state="complete")

    with st.status(t("review_step"),
                   expanded=phase == Phase.EXTRACTION_REVIEW,
                   state=_state_for(phase, Phase.EXTRACTION_REVIEW)) as s3:
        if phase == Phase.EXTRACTION_REVIEW:
            st.warning(t("ai_extracted_verify"))
            extracted = ctx.get('extracted_json', {})
            clause_type = extracted.get('clause_type',
                                       ctx.get('route_result', {}).get('clause_type', ''))
            fields = REVIEW_FIELDS.get(clause_type, [])
            edited = {}
            with st.form("extraction_review_form"):
                for key, label_key, ftype in fields:
                    default = extracted.get(key, "")
                    label = t(label_key)
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
                confirmed = st.form_submit_button(t("confirm_continue"))
                if confirmed:
                    edited["clause_type"] = clause_type
                    ctx.extracted_json = edited
                    ctx.confidence_level = "GREEN"
                    machine._record_exit(Phase.EXTRACTION_REVIEW)
                    machine.current_phase = Phase.BLINDSPOT
                    machine._record_enter(Phase.BLINDSPOT)
                    machine.execute_current_phase()
                    st.rerun()
        elif phase.value > Phase.EXTRACTION_REVIEW.value:
            st.json(ctx.get('extracted_json', {}))
            st.success(t("params_confirmed"))
            _metric_line(metrics, "EXTRACTION_REVIEW")
            s3.update(label=t("review_done"), state="complete")

    with st.status(t("blindspot_step"),
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
                    st.caption(f"{t('source')}: {a.get('source_ref', '')} | {a.get('detail', '')}")
            else:
                st.write(t("no_blindspot"))
            _metric_line(metrics, "BLINDSPOT")
            s4.update(label=t("blindspot_done"), state="complete")

    with st.status(t("calc_step"),
                   expanded=phase == Phase.CALCULATION,
                   state=_state_for(phase, Phase.CALCULATION)) as s5:
        if phase.value > Phase.CALCULATION.value:
            st.write(t("engine_label"))
            result = ctx.get('calc_result', {})
            if result:
                if 'safe_ownership_pct' in result:
                    c1, c2, c3 = st.columns(3)
                    c1.metric(t("safe_ownership"), f"{result['safe_ownership_pct']:.2f}%")
                    c2.metric(t("sa_ownership"), f"{result['series_a_ownership_pct']:.2f}%")
                    c3.metric(t("founder_ownership"), f"{result['founder_ownership_pct']:.2f}%")
                    st.caption(f"{t('total_shares')} {result['total_shares']:,.0f} | "
                               f"{t('price_share')} ${result['price_per_share']:.4f} | "
                               f"{t('premoney_verify')} ${result['pre_money_verification']:,.0f}")
                elif 'new_conversion_price' in result:
                    c1, c2 = st.columns(2)
                    c1.metric(t("new_conversion_price"), f"${result['new_conversion_price']:.4f}")
                    c2.metric(t("price_reduction"), f"{result['price_reduction_pct']:.2f}%")
                elif 'allocations' in result:
                    for a in result['allocations']:
                        st.write(f"**{t('round')} {a['round_index']}**: {a['choice_made']} — "
                                 f"${a['total_received']:,.0f}")
                    c1, c2 = st.columns(2)
                    c1.metric(t("common_payout"), f"${result['common_total']:,.0f}")
                    c2.metric(t("verification_sum"), f"${result['verification_sum']:,.0f}")
                elif 'classification' in result:
                    c1, c2 = st.columns(2)
                    c1.metric(t("classification"), result["classification"])
                    c2.metric(t("confidence"), f"{result['confidence']:.0f}%")

            derivation_steps = result.get("derivation_steps", []) if result else []
            if derivation_steps:
                with st.expander(t("calc_proof")):
                    for i, step in enumerate(derivation_steps, 1):
                        st.markdown(f"**{t('step')} {i}: {step.get('step', '')}**")
                        st.code(step.get('formula', ''), language="text")
                        st.caption(f"{t('values')}: {step.get('values', '')}")
                        st.info(f"{t('result')}: {step.get('result', '')}")

                    from export_utils import generate_proof_excel
                    excel_bytes = generate_proof_excel(
                        ctx.get('calc_result', {}),
                        ctx.get('extracted_json', {}),
                        ctx.get('route_result', {}).get('clause_type', ''),
                    )
                    st.download_button(
                        t("export_excel"),
                        data=excel_bytes,
                        file_name="calculation_proof.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

            _metric_line(metrics, "CALCULATION")
            s5.update(label=t("calc_done"), state="complete")

    with st.status(t("validation_step"),
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
                st.write(t("demo_gap_title"))
                c1, c2 = st.columns(2)
                with c1:
                    st.metric(t("deterministic_founder"),
                              f"{gap['deterministic_result']['founder_pct']:.1f}%")
                with c2:
                    ga = gap.get('generic_ai_answer', {})
                    st.metric(t("generic_ai_founder"),
                              f"{ga.get('founder_pct', 'N/A')}")
                delta = gap.get('delta_pct', 0)
                if delta > 0.5:
                    st.warning(f"{t('delta_label')}: {delta:.1f}% = {t('delta_significant')}")
                else:
                    st.success(f"{t('delta_label')}: {delta:.1f}% = {t('delta_aligned')}")
            _metric_line(metrics, "VALIDATION")
            s6.update(label=t("validation_done"), state="complete")

    if phase == Phase.ERROR:
        _render_confidence_badge("RED")
        st.error(f"{t('pipeline_error')}: {ctx.get('error_msg', 'Unknown error')}")
        st.info(t("try_check"))
        if st.button(t("restart")):
            machine.reset()
            st.rerun()


def render_manual_calculator():
    tab_safe, tab_ad, tab_wf, tab_pc, tab_b1b2, tab_demo = st.tabs([
        t("tab_safe"), t("tab_ad"), t("tab_wf"),
        t("tab_pc"), t("tab_b1b2"), t("tab_demo"),
    ])

    with tab_safe:
        st.header(t("safe_header"))
        st.caption(t("safe_source"))
        with st.form("safe_form"):
            c1, c2 = st.columns(2)
            with c1:
                safe_inv = st.number_input(t("safe_investment"), value=2_000_000, step=100_000, format="%d")
                safe_cap = st.number_input(t("safe_cap"), value=10_000_000, step=500_000, format="%d")
            with c2:
                pre_money = st.number_input(t("pre_money"), value=15_000_000, step=500_000, format="%d")
                sa_inv = st.number_input(t("sa_investment"), value=5_000_000, step=500_000, format="%d")
            c3, c4 = st.columns(2)
            with c3:
                founder_shares = st.number_input(t("founder_shares"), value=8_000_000, step=100_000, format="%d")
            with c4:
                discount_input = st.number_input(t("discount_rate"), value=0.0, min_value=0.0, max_value=99.0, step=5.0, format="%.0f")
            submitted = st.form_submit_button(t("calculate"))
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
                        "cap_only": t("cap_only"),
                        "cap": t("cap"),
                        "discount": t("discount"),
                    }
                    st.info(f"{t('effective_mechanism')} {mech_labels.get(mechanism, mechanism)}")
                    if result.get("cap_price_per_share") and result.get("discount_price_per_share"):
                        pc1, pc2 = st.columns(2)
                        pc1.metric(t("cap_price_share"), f"${result['cap_price_per_share']:.4f}",
                                   delta=t("binding") if mechanism in ("cap_only", "cap") else None)
                        pc2.metric(t("disc_price_share"), f"${result['discount_price_per_share']:.4f}",
                                   delta=t("binding") if mechanism == "discount" else None)
                    c1, c2, c3 = st.columns(3)
                    c1.metric(t("safe_ownership"), f"{result['safe_ownership_pct']:.2f}%")
                    c2.metric(t("sa_ownership"), f"{result['series_a_ownership_pct']:.2f}%")
                    c3.metric(t("founder_ownership"), f"{result['founder_ownership_pct']:.2f}%")
                    st.markdown(f"{t('total_shares')} {result['total_shares']:,.0f} | {t('price_share')} ${result['price_per_share']:.4f} | {t('premoney_verify')} ${result['pre_money_verification']:,.0f}")
                    with st.expander(t("derivation")):
                        derivation_steps = result.get("derivation_steps", [])
                        if derivation_steps:
                            for i, step in enumerate(derivation_steps, 1):
                                st.markdown(f"**{t('step')} {i}: {step.get('step', '')}**")
                                st.code(step.get('formula', ''), language="text")
                                st.caption(f"{t('values')}: {step.get('values', '')}")
                                st.info(f"{t('result')}: {step.get('result', '')}")
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
                    st.error(f"{t('computation_failed')}: {result}")

    with tab_ad:
        st.header(t("ad_header"))
        st.caption(t("ad_source"))
        with st.form("ad_form"):
            c1, c2 = st.columns(2)
            with c1:
                old_cp = st.number_input(t("old_conversion_price"), value=5.00, step=0.10, format="%.2f")
                old_shares = st.number_input(t("shares_outstanding"), value=11_000_000, step=100_000, format="%d")
            with c2:
                new_price = st.number_input(t("new_share_price"), value=3.00, step=0.10, format="%.2f")
                new_shares = st.number_input(t("new_shares_issued"), value=1_000_000, step=100_000, format="%d")
            method_labels = {
                "broad_based": t("broad_based"),
                "narrow_based": t("narrow_based"),
                "full_ratchet": t("full_ratchet"),
            }
            method = st.selectbox(t("method"), ["broad_based", "narrow_based", "full_ratchet"],
                                  format_func=lambda x: method_labels[x])
            submitted = st.form_submit_button(t("calculate"))
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
                    c1.metric(t("new_conversion_price"), f"${result['new_conversion_price']:.4f}")
                    c2.metric(t("price_reduction"), f"{result['price_reduction_pct']:.2f}%")
                    if result.get("formula_steps") or result.get("derivation_steps"):
                        with st.expander(t("derivation")):
                            derivation_steps = result.get("derivation_steps", [])
                            if derivation_steps:
                                for i, step in enumerate(derivation_steps, 1):
                                    st.markdown(f"**{t('step')} {i}: {step.get('step', '')}**")
                                    st.code(step.get('formula', ''), language="text")
                                    st.caption(f"{t('values')}: {step.get('values', '')}")
                                    st.info(f"{t('result')}: {step.get('result', '')}")
                            for step in result.get("formula_steps", []):
                                st.markdown(step)
                else:
                    st.error(f"{t('computation_failed')}: {result}")

    with tab_wf:
        st.header(t("wf_header"))
        st.caption(t("wf_source"))
        with st.form("wf_form"):
            exit_val = st.number_input(t("exit_value"), value=60_000_000, step=1_000_000, format="%d")
            common_pct = st.number_input(t("common_ownership"), value=70.0, step=1.0, format="%.1f")
            st.markdown(t("preferred_rounds"))
            rounds_data = []
            for i in range(3):
                with st.expander(f"{t('round')} {i+1}", expanded=(i < 2)):
                    inv = st.number_input(f"{t('round')} {i+1} {t('investment')}", value=[4_000_000, 8_000_000, 5_000_000][i], key=f"wf_inv_{i}", format="%d")
                    mult = st.number_input(f"{t('round')} {i+1} {t('pref_multiple')}", value=1.0, key=f"wf_mult_{i}", format="%.1f")
                    own = st.number_input(f"{t('round')} {i+1} {t('ownership_pct')}", value=[10.0, 20.0, 15.0][i], key=f"wf_own_{i}", format="%.1f")
                    part_options = ["non_participating", "participating", "participating_capped"]
                    part_labels_map = {
                        "non_participating": t("non_participating"),
                        "participating": t("participating"),
                        "participating_capped": t("participating_capped"),
                    }
                    part = st.selectbox(f"{t('round')} {i+1} {t('participation')}", part_options,
                                        index=2 if i < 2 else 0, key=f"wf_part_{i}",
                                        format_func=lambda x: part_labels_map[x])
                    cap_mult = st.number_input(f"{t('round')} {i+1} {t('cap_multiple')}", value=2.0 if i < 2 else 0.0, key=f"wf_cap_{i}", format="%.1f")
                    enabled = st.checkbox(f"{t('include_round')} {i+1}", value=(i < 2), key=f"wf_en_{i}")
                    if enabled:
                        r = {"investment": inv, "multiple": mult, "ownership_pct": own, "participation": part}
                        if part == "participating_capped" and cap_mult > 0:
                            r["cap_multiple"] = cap_mult
                        rounds_data.append(r)
            submitted = st.form_submit_button(t("run_waterfall"))
            if submitted and rounds_data:
                ok, result, boundary = expert.process({
                    "clause_type": "liquidation_waterfall",
                    "exit_value": exit_val,
                    "preferred_rounds": rounds_data,
                    "common_ownership_pct": common_pct,
                })
                if ok:
                    for a in result["allocations"]:
                        st.markdown(f"**{t('round')} {a['round_index']}**: {a['choice_made']} — Preference ${a['preference_amount']:,.0f} + Participation ${a['participation_amount']:,.0f} = **${a['total_received']:,.0f}**")
                    c1, c2 = st.columns(2)
                    c1.metric(t("common_payout"), f"${result['common_total']:,.0f}")
                    c2.metric(t("verification_sum"), f"${result['verification_sum']:,.0f} (should = ${exit_val:,.0f})")
                else:
                    st.error(f"{t('computation_failed')}: {result}")

    with tab_pc:
        st.header(t("pc_header"))
        st.caption(t("pc_source"))
        with st.form("pc_form"):
            keywords = st.text_input(t("keywords"), value="participating, capped, 3x")
            submitted = st.form_submit_button(t("classify"))
            if submitted:
                ok, result, boundary = expert.process({
                    "clause_type": "participation_classify",
                    "clause_text_keywords": [k.strip() for k in keywords.split(",")],
                })
                if ok:
                    c1, c2 = st.columns(2)
                    c1.metric(t("classification"), result["classification"])
                    c2.metric(t("confidence"), f"{result['confidence']:.0f}%")
                    st.info(result["reasoning"])
                else:
                    st.error(f"{t('class_failed')}: {result}")

    with tab_b1b2:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader(t("b1_header"))
            with st.form("b1_form"):
                pre_val = st.number_input(t("b1_premarket"), value=15_000_000, format="%d")
                fd_shares = st.number_input(t("b1_fully_diluted"), value=14_545_455, format="%d")
                submitted = st.form_submit_button(t("calc_pps"))
                if submitted:
                    pps = pre_val / fd_shares
                    st.metric(t("price_per_share"), f"${pps:.4f}")
                    st.code(f"{t('pps_formula')}\n      = ${pre_val:,.0f} / {fd_shares:,.0f} = ${pps:.4f}")
        with c2:
            st.subheader(t("b2_header"))
            with st.form("b2_form"):
                shares_owned = st.number_input(t("b2_shares_owned"), value=8_000_000, format="%d")
                total_shares = st.number_input(t("b2_total_shares"), value=14_545_455, format="%d")
                submitted = st.form_submit_button(t("calc_ownership"))
                if submitted:
                    own_pct = shares_owned / total_shares * 100
                    st.metric(t("ownership"), f"{own_pct:.2f}%")
                    st.code(f"{t('ownership_formula')}\n           = {shares_owned:,} / {total_shares:,} * 100 = {own_pct:.2f}%")

    with tab_demo:
        st.header(t("gap_header"))
        st.markdown(t("gap_intro"))
        if st.button(t("run_comparison"), type="primary"):
            c1, c2 = st.columns(2)
            with c1:
                st.subheader(t("general_ai_title"))
                st.code(
                    "SAFE post-money -> 20% x (1 - SeriesA%)\n"
                    "                 = 20% x (1 - 25%)\n"
                    "                 = 15%  <- Pre-money SAFE logic!\n\n"
                    "Founder = (1 - 15% - 25%) x 8M / (8M + 2M + 5M)\n"
                    "        = 60%",
                    language="python"
                )
                st.error(f"**Founder = 60%** — {t('wrong')}")
            with c2:
                st.subheader(t("deterministic_title"))
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
                st.success(f"**Founder = {result['founder_ownership_pct']:.1f}%** — {t('correct')}")

            st.markdown("---")
            exit_val = 60_000_000
            gap_pct = 0.60 - result['founder_ownership_pct'] / 100
            gap_dollars = exit_val * gap_pct
            st.warning(f"**Economic impact at ${exit_val/1e6:.0f}M exit:** General AI overstates founder's position by **{gap_pct*100:.1f}%** = **${gap_dollars/1e6:.1f}M** difference.")


GLOSSARY = {
    "en": {
        "Post-money SAFE": "Simple Agreement for Future Equity. Investor gets equity at a future priced round, with valuation cap and optional discount.",
        "Valuation Cap": "Maximum company valuation at which SAFE converts. Lower cap = more shares for SAFE investor.",
        "Discount Rate": "Percentage discount SAFE investor gets on the priced round price. e.g., 20% discount means SAFE converts at 80% of Series A price.",
        "Liquidation Preference": "In an exit (acquisition/IPO), preferred shareholders get paid before common shareholders. Usually 1x investment amount.",
        "Participating Preferred": "Investor gets liquidation preference AND participates in remaining proceeds pro-rata. Also called 'double-dip'.",
        "Non-Participating Preferred": "Investor chooses between liquidation preference or converting to common and sharing pro-rata. More founder-friendly.",
        "Anti-Dilution": "Protection for investors in a down-round. Adjusts conversion price to compensate for lower new share price.",
        "Full Ratchet": "Most investor-favorable anti-dilution: conversion price drops to the new (lower) share price directly.",
        "Broad-based Weighted Average": "Standard anti-dilution method: considers both old and new shares, adjusts price proportionally.",
    },
    "zh": {
        "Post-money SAFE": "未来股权简单协议。投资者在后续定价轮获得股权，设有估值上限和可选折扣。",
        "Valuation Cap": "SAFE 转换时的公司估值上限。上限越低，SAFE 投资者获得越多股份。",
        "Discount Rate": "SAFE 投资者在定价轮价格上获得的折扣百分比。例如20%折扣意味着SAFE按A轮价格的80%转换。",
        "Liquidation Preference": "在退出（收购/IPO）时，优先股股东先于普通股股东获得赔付。通常为投资金额的1倍。",
        "Participating Preferred": "投资者既获得清算优先权，又按比例参与剩余收益分配。又称'双重 dipping'。",
        "Non-Participating Preferred": "投资者在清算优先权和转为普通股按比例分配之间选择其一。对创始人更友好。",
        "Anti-Dilution": "降价轮中投资者的保护机制。调整转换价格以补偿新股价格下降。",
        "Full Ratchet": "最有利于投资者的反稀释方式：转换价格直接降至新的（更低的）股价。",
        "Broad-based Weighted Average": "标准反稀释方法：综合考虑新旧股份，按比例调整价格。",
    },
}


with st.sidebar:
    lang = st.selectbox("Language / 语言", ["English", "中文"],
                        index=0 if st.session_state.get("lang", "en") == "en" else 1)
    st.session_state.lang = "en" if lang == "English" else "zh"

    st.title(t("agent_config"))
    api_key = get_api_key()
    if api_key:
        st.success(t("llm_key_ready"))
        os.environ["ZHIPU_API_KEY"] = api_key
    else:
        st.error(t("no_api_key"))
        st.text_input(t("enter_key"), type="password", key="fallback_key")

    mode_switch = st.radio(t("mode"), [t("agent_mode"), t("manual_mode")])

if mode_switch == t("manual_mode"):
    render_manual_calculator()
else:
    tab_agent, tab_glossary = st.tabs([t("agent_tab"), t("glossary_tab")])

    with tab_agent:
        if "phase_machine" not in st.session_state:
            st.session_state.phase_machine = PhaseStateMachine()

        machine = st.session_state.phase_machine

        user_input = st.text_area(
            t("paste_clause"),
            height=200,
            placeholder=t("paste_placeholder"),
        )

        if st.button(t("start_agent"), type="primary", disabled=not api_key):
            st.session_state.raw_text = sanitize_input(user_input)
            machine.reset()
            machine.current_phase = Phase.ROUTING
            machine._record_enter(Phase.ROUTING)
            machine.execute_current_phase()
            st.rerun()

        if machine.current_phase != Phase.IDLE:
            machine.execute_current_phase()
            render_agent_pipeline(machine)

    with tab_glossary:
        st.header(t("vc_term_glossary"))
        lang_code = st.session_state.get("lang", "en")
        for term, definition in GLOSSARY.get(lang_code, GLOSSARY["en"]).items():
            st.markdown(f"**{term}**: {definition}")
