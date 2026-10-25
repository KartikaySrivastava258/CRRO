import { api } from "../lib/api.js";
import { human, money, pct, pp, when } from "../lib/format.js";
import { go, useResource } from "../lib/hooks.js";
import { Card, Empty, KV, Page, Resource, Status, Tag } from "../components/ui.jsx";
import { CandidateTable } from "./CaseDetail.jsx";

export default function Explainability({ caseId }) {
  const list = useResource(() => api.cases({ limit: 50 }), []);
  const det = useResource(() => (caseId ? api.case(caseId) : Promise.resolve(null)), [caseId]);
  const model = useResource(() => api.model(), []);
  return (
    <Page eyebrow="Decision science" title="Why this action?"
          intro="Explanations are assembled from what was persisted at decision time (no re-prompting, no re-scoring). The decomposition is exact arithmetic of the model's estimates. No feature-attribution method (e.g. SHAP) is implemented, so none is shown.">
      <div className="filters">
        <label className="field"><span>Case</span>
          <Resource res={list}>{(d) => (
            <select value={caseId || ""} onChange={(e) => go("/explain", { case: e.target.value })}>
              <option value="">Select a case…</option>
              {d.items.filter((c) => c.selected_action).map((c) => (
                <option key={c.case_id} value={c.case_id}>{c.external_payment_id} · {human(c.failure_reason)} · {money(c.amount_paise, { whole: true })} → {human(c.selected_action)}</option>))}
            </select>)}</Resource></label>
      </div>
      {!caseId ? <Empty title="Choose a decided case to see its explanation" /> :
        <Resource res={det} label="Loading decision">{(d) => d?.decision ? <Explain d={d} /> : <Empty title="No decision recorded for this case" />}</Resource>}
      <div style={{ marginTop: 14 }}>
        <Card title="Model card" right={<Tag kind="SYNTHETIC" />} sub="The model is trained only on synthetic randomised-assignment data.">
          <Resource res={model}>{(m) => (
            <KV items={[["Version", <span className="mono">{m.model_version}</span>], ["Kind", m.kind === "pooled" ? "S-learner (pooled gradient boosting)" : "T-learner"],
              ["Training rows", m.training_data.n_train.toLocaleString("en-IN")], ["Training seed", m.training_data.seed],
              ["Holdout Brier (control)", m.holdout_control_calibration.brier_score], ["Holdout ECE (control)", m.holdout_control_calibration.ece],
              ["Features", m.features.join(", ")], ["Uncertainty", m.uncertainty], ["Trained", when(m.trained_at_utc)]]} />)}
          </Resource>
        </Card>
      </div>
    </Page>
  );
}

function Explain({ d }) {
  const dec = d.decision, c = d.case;
  const sel = dec.candidates.find((x) => x.action === dec.selected_action);
  const rejected = dec.candidates.filter((x) => x.action !== dec.selected_action);
  return (
    <div className="grid">
      <Card title={`${c.external_payment_id}: ${human(dec.selected_action)}`} right={<span style={{ display: "flex", gap: 6 }}><Status value={c.status} /><Tag kind="ESTIMATE" /></span>}
            sub={`${money(c.amount_paise)} · ${human(c.failure_reason)} · ${c.payment_method} · decided ${when(dec.created_at)}`}>
        {sel ? (
          <div className="grid cols-2">
            <div>
              <h2 style={{ marginBottom: 8 }}>Expected value decomposition</h2>
              <div className="waterfall">
                <span>Without action, P(recover)</span><span className="num">{pct(dec.p_control)}</span>
                <span>With {human(sel.action)}, P(recover)</span><span className="num">{pct(sel.p_action)}</span>
                <span>Uplift × amount ({pp(sel.uplift)} × {money(c.amount_paise, { whole: true })})</span><span className="num pos">{money(sel.expected_gross_incremental_paise)}</span>
                <span>− expected incentive (P × discount)</span><span className="num neg">−{money(sel.expected_incentive_cost_paise)}</span>
                <span>− action cost</span><span className="num neg">−{money(sel.action_cost_paise)}</span>
                <span className="total">Expected incremental net</span><span className="num total">{money(sel.expected_value_paise)}</span>
              </div>
            </div>
            <div>
              <h2 style={{ marginBottom: 8 }}>Guardrails & governance</h2>
              <KV items={[["Approval", dec.requires_approval ? `required (${d.approval?.status || "pending"})` : "not required (below threshold)"],
                ["Approval reason", d.approval?.reason || "—"], ["Model", <span className="mono">{dec.model_version}</span>],
                ["Policy", <span className="mono">{dec.policy_version}</span>],
                ["Provider outcome", d.recovery ? "Recovered — reconciled against a verified provider event" : human(c.status)]]} />
            </div>
          </div>) : (
          <div className="callout">No action was taken: <strong>{dec.reason_code}</strong>. Doing nothing is a valid decision when no feasible action has positive expected incremental value.</div>)}
        {dec.feature_imputations?.length > 0 && <div className="callout warn" style={{ marginTop: 12 }}>
          Inputs not provided by the merchant were imputed: {dec.feature_imputations.map((i) => i.feature).join(", ")}. Treat the estimate with more caution.</div>}
      </Card>
      <Card title="All candidates and why they were not chosen">
        <CandidateTable decision={dec} />
        <p className="card-sub" style={{ marginTop: 10 }}>
          {rejected.filter((x) => x.feasible).length > 0 && "Feasible but not selected: lower expected incremental net value than the selected action. "}
          Reason codes come from deterministic checks (eligibility, merchant policy, opt-out, cooldown, contact caps, budgets) and the value test.</p>
      </Card>
    </div>
  );
}
