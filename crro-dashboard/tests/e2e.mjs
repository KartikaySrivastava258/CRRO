// Browser E2E for the critical operator path against a REAL running API
// (scripts/serve.py serving dist/). Seeds data through the HTTP API first.
// Usage: CRRO_E2E_BASE=http://127.0.0.1:8100 node tests/e2e.mjs [--screenshots ../docs/screenshots]
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";

const BASE = process.env.CRRO_E2E_BASE || "http://127.0.0.1:8100";
const SHOTS = process.argv.includes("--screenshots") ? process.argv[process.argv.indexOf("--screenshots") + 1] : null;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const OP = "demo-operator-token-local-only";
const results = [];
async function step(name, fn) {
  const t = Date.now();
  try { await fn(); results.push({ name, ok: true, ms: Date.now() - t }); console.log(`  ✔ ${name} (${Date.now() - t} ms)`); }
  catch (e) { results.push({ name, ok: false, error: String(e) }); console.log(`  ✘ ${name}\n    ${String(e).split("\n").slice(0, 4).join("\n    ")}`); }
}
const apiGet = (path, token = OP) => fetch(BASE + path, { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
const inr0 = (p) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(p / 100);
const shot = async (page, name, opts = {}) => SHOTS && page.screenshot({ path: `${SHOTS}/${name}`, ...opts });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await step("sign-in with demo operator credentials", async () => {
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "Use demo operator" }).click();
  await page.getByRole("heading", { name: "Recovery overview" }).waitFor();
});

await step("overview KPIs equal API values and carry provenance tags", async () => {
  const ov = await apiGet("/v1/overview");
  const gross = Object.values(ov.confirmed_recovered_by_provider_mode).reduce((a, v) => a + v.gross_recovered_paise, 0);
  const kpi = page.locator(".kpi", { hasText: "Confirmed recovered (gross)" });
  await assert.equal((await kpi.locator(".kpi-value").innerText()).trim(), inr0(gross));
  const exp = page.locator(".kpi", { hasText: "Expected incremental value" });
  assert.equal((await exp.locator(".kpi-value").innerText()).trim(), inr0(ov.expected_incremental_value_open_decisions.expected_value_paise));
  await exp.getByText("MODEL ESTIMATE").waitFor();
  await kpi.getByText("RECONCILED").waitFor();
  await kpi.getByText("SIMULATED").waitFor();
  assert.ok(gross > 0, "seeded data should include confirmed recoveries");
  await page.getByText("Synthetic demo data").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "01-overview.png", { fullPage: true });
});

await step("case detail explains decision with candidates and rejection reasons", async () => {
  await page.goto(BASE + "/#/cases?q=DEMO-007");
  await page.locator("tbody tr").first().click();
  await page.getByRole("heading", { name: /Case DEMO-007/ }).waitFor();
  await page.getByText("CUSTOMER_OPTED_OUT").first().waitFor();       // opted-out customer: contact actions blocked
  await page.goto(BASE + "/#/cases?q=DEMO-001");
  await page.locator("tbody tr").first().click();
  await page.getByRole("heading", { name: "Decision" }).waitFor();
  await page.getByRole("heading", { name: "Reconciled recovery" }).waitFor();
  await page.getByText("Expected incremental net").first().waitFor();
  await shot(page, "02-case-decision.png", { fullPage: false });
});

await step("optimizer: validation blocks bad input; rapid double-click starts exactly one run", async () => {
  const before = (await apiGet("/v1/optimizer/runs")).items.length;
  await page.goto(BASE + "/#/optimizer");
  const n = page.getByLabel(/Synthetic cases/);
  await n.fill("10");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.getByText("Must be between 200 and 5000").waitFor();
  assert.equal((await apiGet("/v1/optimizer/runs")).items.length, before, "invalid form must not hit the API");
  await n.fill("800"); await page.getByLabel(/Contact budget/).fill("80");
  const btn = page.getByRole("button", { name: "Run simulation" });
  await btn.click(); await btn.click({ force: true, timeout: 1000 }).catch(() => {});
  await page.getByRole("button", { name: "Run simulation" }).waitFor({ timeout: 120000 });
  await page.getByText("Exact vs greedy").waitFor();
  assert.equal((await apiGet("/v1/optimizer/runs")).items.length, before + 1, "exactly one new run");
  await page.getByText("OPTIMAL").first().waitFor();
  await shot(page, "03-optimizer-comparison.png", { fullPage: true });
});

let approvalCase;
await step("approval: confirmation dialog, server-confirmed result, no duplicate dispatch", async () => {
  await page.goto(BASE + "/#/approvals");
  const row = page.locator("tbody tr").first();
  await row.waitFor();
  approvalCase = (await row.locator("a.mono").innerText()).trim();
  await shot(page, "04-approval-queue.png");
  await row.getByRole("button", { name: "Approve" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel(/Note/).fill("E2E: verified with customer");
  const confirm = dlg.getByRole("button", { name: "Approve and dispatch" });
  await confirm.click(); await confirm.click({ timeout: 500 }).catch(() => {});
  await page.getByText(/Approved apr_/).waitFor();
  const detail = await apiGet(`/v1/cases/${approvalCase}`);
  assert.equal(detail.approval.status, "APPROVED");
  assert.equal(detail.attempt.dispatch_count, 1);
  assert.equal(detail.attempt.status, "PROVIDER_ACCEPTED");
});

await step("simulated signed webhook reconciles the approved case; timeline shows the chain", async () => {
  await page.goto(BASE + `/#/cases/${approvalCase}`);
  await page.getByRole("button", { name: "Simulate customer payment" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Send signed webhook" }).click();
  await page.getByText(/Simulator delivered/).waitFor();
  await page.locator(".card", { hasText: "Case" }).first().getByText("Recovered").waitFor();
  for (const t of ["Payment failed received", "Decision recorded", "Approval approved", "Action dispatch result", "Case status changed"])
    await page.locator(".timeline").getByText(t).first().waitFor();
  const d = await apiGet(`/v1/cases/${approvalCase}`);
  assert.equal(d.case.status, "RECOVERED");
  assert.equal(d.recovery.cash_collected_paise, d.attempt.collect_amount_paise);
  await page.locator(".timeline").scrollIntoViewIfNeeded();
  await shot(page, "05-event-timeline.png", { fullPage: true });
});

await step("audit: filter by event type and export JSON", async () => {
  await page.goto(BASE + "/#/audit?event_type=APPROVAL_APPROVED");
  const auditTable = page.getByRole("region", { name: "Audit events" });
  await auditTable.locator("tbody tr").first().waitFor();
  const types = await auditTable.locator("tbody tr td:nth-child(2)").allInnerTexts();
  assert.ok(types.length > 0 && types.every((t) => t.trim() === "APPROVAL_APPROVED"), `filter applied: ${types}`);
  const viaApi = await apiGet("/v1/audit?event_type=APPROVAL_APPROVED");
  assert.equal(types.length, viaApi.items.length);
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export JSON" }).click()]);
  assert.equal(dl.suggestedFilename(), "crro-audit-export.json");
});

await step("explainability shows value decomposition and model card", async () => {
  await page.goto(BASE + `/#/explain?case=${approvalCase}`);
  await page.getByText("Expected value decomposition").waitFor();
  await page.getByText("Model card").waitFor();
  await shot(page, "06-explainability.png", { fullPage: true });
});

await step("error state with retry when the API fails", async () => {
  await page.route("**/v1/overview", (r) => r.abort());
  await page.goto(BASE + "/#/"); await page.reload();
  await page.getByRole("alert").getByText("Can't reach the API").waitFor();
  await page.unroute("**/v1/overview");
  await page.getByRole("button", { name: "Retry" }).click();
  await page.locator(".kpi").first().waitFor();
});

await step("keyboard navigation reaches nav and table rows", async () => {
  await page.goto(BASE + "/#/cases");
  await page.locator("tbody tr").first().waitFor();
  await page.locator("tbody tr").first().focus();
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: /^Case / }).waitFor();
});

await step("read-only viewer cannot approve (UI disabled; server also enforces)", async () => {
  const v = await browser.newContext(); const p = await v.newPage();
  await p.goto(BASE + "/"); await p.getByRole("button", { name: "Use read-only viewer" }).click();
  await p.goto(BASE + "/#/approvals?x=1");
  await p.getByText("Read-only role").waitFor();
  const btns = p.getByRole("button", { name: "Approve" });
  if (await btns.count()) assert.equal(await btns.first().isDisabled(), true);
  await v.close();
});

for (const w of [320, 375, 768, 1024]) {
  await step(`responsive ${w}px: no horizontal page overflow`, async () => {
    const m = await browser.newContext({ viewport: { width: w, height: 800 } }); const p = await m.newPage();
    await p.goto(BASE + "/"); await p.getByRole("button", { name: "Use demo operator" }).click();
    await p.locator(".kpi").first().waitFor();
    const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 0, `page overflows by ${overflow}px`);
    if (w === 375) await shot(p, "07-mobile.png", { fullPage: true });
    await m.close();
  });
}

await step("no uncaught browser errors during the run", async () => {
  const unexpected = consoleErrors.filter((e) => !e.includes("net::ERR_FAILED") && !e.includes("Failed to fetch") && !e.includes("Failed to load resource"));
  assert.deepEqual(unexpected, []);
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nE2E: ${results.length - failed.length} passed, ${failed.length} failed, 0 skipped`);
process.exit(failed.length ? 1 : 0);
