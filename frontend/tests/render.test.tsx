/**
 * Tes render tanpa browser (U01-U03): komponen dirender ke HTML dengan react-dom/server.
 * Jalankan: npm run test:render. Ini membuktikan tidak ada crash dan teks status yang benar; interaksi
 * klik/browser tetap perlu diuji manual.
 */
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { EventCard } from "../src/components/EventCard";
import { FrontierAudit } from "../src/components/FrontierAudit";
import { WorkflowPanels } from "../src/components/WorkflowPanels";
import type { FrontierRecord, ScreenedEventSummary, WorkflowReport } from "../src/types";

const results: string[] = [];
function test(name: string, body: () => void) {
  body();
  results.push(name);
}

const panel = (domain: string) => ({
  domain, status: "completed", headline: "", metrics: [], missing_data: [], limitations: [], conflicts: [],
  claims: [{ claim_id: `${domain}:model:1`, domain, kind: "interpretation", statement: `Klaim ${domain}.`,
             attribution: "data", metric_ids: [], source_ids: [], quote: null, period: null, limitations: [],
             validation_status: "supported", validation_notes: [], version: 1, author: "model:fake" }],
});

// Laporan versi lama: tanpa frontier, chronology, runtime, reviewer_verdicts, reviewer_models.
const oldReport = {
  run_id: "LAMA-1", ticker: "TEST", as_of: "2026-09-18", horizon: "medium", status: "completed", report_version: 1,
  generated_at: "2026-09-18T01:00:00+00:00", data_mode: "live", mode: "live",
  plan: { analyst_model: "qwen2.5:14b", reviewer_model: "glm4:9b" },
  panels: Object.fromEntries(["fundamental", "valuation", "technical", "news"].map((domain) => [domain, panel(domain)])),
  metrics: {}, sources: [],
  synthesis: { author: "code", sections: [], dropped: [], error: null },
  validation: { unresolved_claim_ids: [], repair_count: 0 }, gate: { status: "passed", rejected_terms: [] },
  credits_used: 13, model_runs: [], disclaimer: "Bukan rekomendasi beli atau jual.",
} as unknown as WorkflowReport;

const oldEvent = {
  id: 1, ticker: "HATM", headline: "HATM private placement", source_url: "https://berita.test/a",
  bucket: "non_preemptive_capital", label: "inconclusive", confidence: 0, score_kind: "heuristic_signal_strength_not_probability",
  provider: "ollama:qwen2.5:7b", gate_status: "needs_review", watch_status: "active", created_at: "2026-09-18",
  detail: {
    research: { status: "needs_review", extraction_attempts: 1, case_id: "HATM-1", issues: [], evidence: [] },
    event: { ticker: "HATM", headline: "h", body: "", source_url: "https://berita.test/a", published_at: "2026-09-01",
             bucket: "non_preemptive_capital", matched_keywords: [], sector: null, sub_sector: [] },
    snapshot: null,
    verdict: { label: "inconclusive", confidence: 0, summary: "", red_flag_signals: [], growth_signals: [],
               rationale_bullets: [], provider: "ollama:qwen2.5:7b" },
    gate: { status: "needs_review", rejected_terms: [] }, watch_status: "active",
  },
} as unknown as ScreenedEventSummary;

const record = (status: string, extra: Partial<FrontierRecord> = {}): FrontierRecord => ({
  enabled: true, status, applied: false, mode: "shadow", model: "deepseek-flash", model_version: "DeepSeek-V4.1-Flash",
  triggers: [{ claim_id: "news:model:1", reason: "reviewer_conflict" }], calls: [], verdicts: [], ...extra,
});

test("U01 laporan lama tanpa field baru tetap dirender", () => {
  const html = renderToStaticMarkup(<WorkflowPanels report={oldReport} />);
  assert.match(html, /TEST/);
  assert.match(html, /glm4:9b/);
  assert.doesNotMatch(html, /Reviewer frontier/);
  assert.doesNotMatch(html, /Riwayat konfigurasi/);
});

test("U01 kartu screening lama tanpa frontier/reviewer_checks tetap dirender", () => {
  const html = renderToStaticMarkup(<EventCard event={oldEvent} />);
  assert.match(html, /HATM/);
  assert.doesNotMatch(html, /Reviewer frontier/);
});

test("U02 semua status frontier dirender tanpa crash dan tanpa sukses palsu", () => {
  const statuses = ["not_triggered", "completed", "partial", "unavailable", "failed", "budget_exhausted",
                    "offline_cache_miss", "in_flight", "status_baru_tidak_dikenal"];
  for (const status of statuses) {
    const html = renderToStaticMarkup(<FrontierAudit record={record(status, { message: `pesan ${status}` })} />);
    assert.match(html, /Reviewer frontier/, status);
    if (status !== "completed") assert.doesNotMatch(html, / · selesai · /, `${status} tidak boleh tampil selesai`);
    assert.match(html, /tidak dipakai keputusan/, `${status}: shadow harus ditandai tidak dipakai`);
  }
  assert.equal(renderToStaticMarkup(<FrontierAudit record={{ enabled: false, status: "disabled", applied: false }} />), "");
  assert.equal(renderToStaticMarkup(<FrontierAudit record={null} />), "");
});

test("U03 angka token/biaya: cache tanpa biaya baru, usage tak diketahui tidak ditulis nol", () => {
  const html = renderToStaticMarkup(
    <FrontierAudit
      record={record("partial", {
        calls: [
          { step: "independent", reason: "reviewer_conflict", status: "cache_hit", cached: true, attempts: 0,
            seconds: 0, usage: null, cached_usage: { prompt_tokens: 999, completion_tokens: 888 }, usage_known: true,
            cost_usd_estimate: 0, error: null },
          { step: "second_look", reason: "x", status: "failed", cached: false, attempts: 2, seconds: 180,
            usage: { prompt_tokens: 200, completion_tokens: 100 }, usage_known: false, usage_partial: true,
            cost_usd_estimate: null, cost_usd_known_part: 0.0003, error: "timeout: Tidak ada jawaban." },
        ],
        totals: { calls: 1, attempts: 2, cache_hits: 1, prompt_tokens: 200, completion_tokens: 100,
                  reasoning_tokens: 0, cost_usd_estimate: null, cost_usd_known_part: 0.0003, usage_unknown: true,
                  usage_partial: true, overshoot: false, price_version: "deepseek-2026-09-28" },
      })}
    />,
  );
  assert.match(html, /dari cache \(tanpa biaya baru\)/);
  assert.doesNotMatch(html, /999/, "usage asli cache tidak boleh dihitung sebagai token baru");
  assert.match(html, /token\s+masuk 200, keluar 100/);
  assert.match(html, /tidak diketahui/);
  assert.doesNotMatch(html, /biaya ≈ \$0\.0000[^\d]/, "biaya tidak diketahui tidak boleh tampil nol");
});

test("laporan baru: provenance runtime, pembanding berurutan, kronologi", () => {
  const report = {
    ...oldReport,
    plan: { ...oldReport.plan, reviewer_models: ["glm4:9b", "gemma3:12b"] },
    runtime: { bound: { target: "colab", ollama_url: "https://x.trycloudflare.com", frontier: { enabled: true, mode: "shadow" } },
               history: [{ event: "start", at: "2026-09-28T01:00:00+00:00", from_nodes: [] },
                         { event: "resume_config_changed", at: "2026-09-28T02:00:00+00:00", from_nodes: ["publish"],
                           changes: [{ field: "ollama_url", before: "https://a", after: "https://x" }] }] },
    chronology: [{ action_key: "private_placement:-", action_type: "private_placement", action_ref: null,
                   identity: "unproven", identity_note: "bisa jadi aksi berbeda", source_ids: ["news:a", "news:b"],
                   claim_ids: [], conflicts: [{ metric: "shares", stage_class: "ketentuan", entry_ids: ["a", "b"],
                                                note: "angka berbeda antarsumber tanpa bukti revisi" }],
                   metrics: { shares: { unit: "saham", terms_value: null, terms_note: "tidak dipilih otomatis",
                                        realized_value: null, realized_note: "", history: ["a", "b"] } },
                   entries: [] }],
  } as unknown as WorkflowReport;
  const html = renderToStaticMarkup(<WorkflowPanels report={report} />);
  assert.match(html, /glm4:9b → gemma3:12b/);
  assert.match(html, /GPU Colab/);
  assert.match(html, /1x resume/);
  assert.match(html, /belum terselesaikan/);
});

console.log(`${results.length} tes render lolos:\n- ${results.join("\n- ")}`);
