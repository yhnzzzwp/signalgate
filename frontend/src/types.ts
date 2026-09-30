export type VerdictLabel = "growth_catalyst" | "structural_red_flag" | "inconclusive";
export type GateStatus = "passed" | "needs_review";
export type ActionBucket = "control_change" | "non_preemptive_capital" | "rights_issue" | "general_action";

export interface ScreenedEventSummary {
  id: number;
  ticker: string;
  headline: string;
  source_url: string;
  bucket: ActionBucket;
  label: VerdictLabel;
  confidence: number;
  score_kind: "heuristic_signal_strength_not_probability";
  provider: string;
  gate_status: GateStatus;
  watch_status: string;
  created_at: string;
  detail: ScreenedEventDetail;
}

export interface ResearchFact {
  id: string;
  topic: string;
  value: string;
  claim: string;
  quote: string;
  evidence_id: string;
  validator_status?: string | null;
}

export interface EvidenceSource {
  id: string;
  kind: string;
  url: string;
  title: string;
  retrieved_at: string;
  page_number?: number | null;
}

export interface ScreenedEventDetail {
  research?: {
    status: string;
    extraction_attempts: number;
    review_rounds?: number;
    case_id: string | null;
    issues: string[];
    facts?: ResearchFact[];
    model_runs?: { role: string; model: string; seconds?: number; offloaded?: boolean }[];
    evidence: EvidenceSource[];
    /** Putusan tiap pembanding lokal sebelum digabung; tidak ada pada kasus lama. */
    reviewer_checks?: ReviewerCheck[];
    /** Audit reviewer frontier; null/absen = frontier tidak aktif atau kasus lama. */
    frontier?: FrontierRecord | null;
  } | null;
  event: {
    ticker: string;
    headline: string;
    body: string;
    source_url: string;
    published_at: string;
    bucket: ActionBucket;
    matched_keywords: string[];
    sector: string | null;
    sub_sector: string[];
  };
  snapshot: {
    ticker: string;
    company_name: string;
    business_description: string | null;
    market_cap: number | null;
    major_shareholders: { name: string; share_percentage: string }[];
    pb_ratio: number | null;
    pe_ratio: number | null;
    intrinsic_value: number | null;
    last_close_price: number | null;
  } | null;
  verdict: {
    label: VerdictLabel;
    confidence: number;
    summary: string;
    red_flag_signals: string[];
    growth_signals: string[];
    rationale_bullets: string[];
    provider: string;
  };
  gate: {
    status: GateStatus;
    rejected_terms: string[];
  };
  watch_status: string;
}

export interface ReviewerCheck {
  reviewer: string;
  model: string;
  agrees_with_label: boolean;
  independent_label?: string;
  checks: { fact_id: string; status: string; reason: string }[];
  extra_facts?: ResearchFact[];
}

export type FrontierStatus =
  | "disabled"
  | "not_triggered"
  | "completed"
  | "partial"
  | "unavailable"
  | "failed"
  | "budget_exhausted"
  | "offline_cache_miss"
  | "in_flight";

export interface FrontierUsage {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  prompt_cache_hit_tokens?: number | null;
  prompt_cache_miss_tokens?: number | null;
  reasoning_tokens?: number | null;
}

export interface FrontierCall {
  step: string;
  reason: string;
  status: string | null;
  cached: boolean;
  attempts: number;
  seconds: number;
  usage: FrontierUsage | null;
  usage_known: boolean;
  cost_usd_estimate: number | null;
  original_cost_usd_estimate?: number | null;
  cost_note?: string;
  budget_limit?: string;
  cost_usd_known_part?: number;
  usage_partial?: boolean;
  overshoot?: boolean;
  cached_usage?: FrontierUsage | null;
  error: string | null;
  response_model?: string | null;
}

export interface FrontierVerdictView {
  status: string;
  evidence_ids: string[];
  reason: string;
}

export interface FrontierReconciliation {
  claim_id: string;
  local_statuses: (string | null)[];
  local_merged: string;
  frontier_status: string | null;
  final_status: string;
  rule: string;
  changed: boolean;
  note: string;
}

export interface ChronologyEntry {
  entry_id: string;
  action_type: string;
  action_ref: string | null;
  stage: string;
  stage_basis: string;
  metric: string;
  value: number;
  unit: string;
  value_text: string;
  quote: string;
  source_id: string;
  published_at: string | null;
  event_date: string | null;
  event_date_claimed?: string | null;
  claim_id: string | null;
  origin: string;
  value_basis?: string;
  revises_entry_ids: string[];
}

export interface ChronologyAction {
  action_key: string;
  action_type: string;
  action_ref: string | null;
  identity: string;
  identity_note: string;
  entries: ChronologyEntry[];
  metrics: Record<
    string,
    {
      unit: string;
      terms_value: number | null;
      terms_note: string;
      realized_value: number | null;
      realized_note: string;
      history: string[];
    }
  >;
  conflicts: { metric: string; stage_class: string; entry_ids: string[]; note: string }[];
  claim_ids: string[];
  source_ids: string[];
}

export interface FrontierRecord {
  enabled: boolean;
  status: FrontierStatus | string;
  applied: boolean;
  mode?: string;
  provider?: string;
  model?: string;
  model_version?: string | null;
  message?: string | null;
  offline?: boolean;
  prompt_version?: string;
  price_version?: string;
  triggers?: { claim_id: string | null; reason: string }[];
  calls?: FrontierCall[];
  verdicts?: {
    claim_id: string;
    independent: FrontierVerdictView | null;
    second_look_needed: boolean;
    second_look: FrontierVerdictView | null;
  }[];
  evidence_reading?: string[];
  limitations?: string[];
  reconciliation?: FrontierReconciliation[];
  differences?: FrontierReconciliation[];
  decision_changed?: boolean;
  decision_changes?: string[];
  local_outcome?: { status: string; label: string };
  reconciled_outcome?: { status: string; label: string };
  chronology?: ChronologyAction[];
  timeline_conflicts?: { local_messages: string[]; with_frontier_messages: string[] };
  second_look_status?: string;
  reused_from_case_id?: string;
  reuse_note?: string;
  totals?: {
    calls: number;
    attempts: number;
    cache_hits: number;
    prompt_tokens: number;
    completion_tokens: number;
    reasoning_tokens: number;
    cost_usd_estimate: number | null;
    cost_usd_known_part?: number;
    usage_unknown: boolean;
    usage_partial?: boolean;
    overshoot?: boolean;
    price_version: string;
  };
  budget?: Record<string, unknown>;
}

export interface ClaimReviewerVerdict {
  role: string;
  model: string | null;
  status: string | null;
  reason: string;
}

export interface ClaimFrontier {
  mode: string;
  applied: boolean;
  rule: string;
  frontier_status: string | null;
  final_status: string;
  note: string;
  triggers?: string[];
  independent?: FrontierVerdictView | null;
  second_look?: FrontierVerdictView | null;
}

export type RuntimeTarget = "local";
export type RuntimeFrontierMode = "off" | "shadow" | "escalation";

/** Konfigurasi efektif nonrahasia; token/key hanya muncul sebagai status dan sidik jari. */
export interface RuntimeSnapshot {
  revision: number;
  target: RuntimeTarget | "env";
  ollama_url: string | null;
  auth: "token" | "none";
  token_fingerprint: string | null;
  workflow: { analyst: string | null; reviewers: string[] };
  screening: { analyst: string | null; reviewers: string[] };
  frontier: {
    enabled: boolean;
    mode: string;
    model: string;
    model_version: string | null;
    reasoning_effort: string;
    max_tokens: number;
    cache_mode: string;
    calls_on_replay: boolean;
    key_configured: boolean;
  };
}

export interface RuntimeConfig {
  local_only?: boolean;
  state: {
    revision: number;
    target: RuntimeTarget | null;
    frontier_mode: RuntimeFrontierMode | null;
    updated_at: string | null;
    /** false = tersimpan atas persetujuan eksplisit walau belum siap. */
    ready_at_activation: boolean | null;
  };
  effective: RuntimeSnapshot;
  active_run: RunJob | null;
  note: string;
}

export interface RuntimeCheck {
  backend: { ok: boolean };
  target: RuntimeTarget;
  endpoint: { ok: boolean; url: string | null; error: string | null };
  ollama: { ok: boolean; status: string; version: string | null; error: string | null };
  models: { ok: boolean; status: string; required: string[]; missing: string[]; installed: string[]; error: string | null };
  gpu: { status: "proven" | "not_proven" | "unknown"; detail: string; probe_model?: string };
  frontier: {
    enabled: boolean;
    mode: string;
    model: string;
    model_version: string | null;
    key_configured: boolean;
    issue: string | null;
    account: {
      key_valid: boolean | null;
      balance: { is_available?: boolean; balances: { currency: string; total_balance: string }[] } | null;
      model_available: boolean | null;
      model_name: string | null;
      error: string | null;
    } | null;
  };
  warnings: string[];
  ready_for_next_run: boolean;
  checked_at: string;
  /** Token tersimpan ditahan karena terikat ke host lain. */
  token_withheld?: boolean;
}

export interface RunConfigHistoryEntry {
  event: string;
  at: string;
  from_nodes: string[];
  changes?: { field: string; before: unknown; after: unknown }[];
  snapshot?: RuntimeSnapshot;
}

export interface AuditLogEntry {
  id: number;
  stage: string;
  ticker: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface ScanRunResult {
  discovered: number;
  new: number;
  already_processed: number;
  awaiting_publication: number;
  ambiguous_documents: number;
  without_document: number;
  pending: number;
  retry_waiting?: number;
  retry_exhausted?: number;
  next_retry_at?: string | null;
  processed: number;
  provider: string | null;
  articles_checked: number;
  listing_pages_fetched: number;
  failed_sources: { url: string; error: string }[];
  coverage_note: string;
}

export interface ScanRetryResult {
  ticker: string;
  screened_count: number;
}

export interface PipelineRunResult {
  screened_count: number;
  already_processed?: number;
  provider: string;
}

export type PanelStatus = "completed" | "insufficient_data" | "needs_review" | "failed";
export type ValidationStatus = "pending" | "supported" | "unsupported" | "contradicted";

export interface WorkflowMetric {
  metric_id: string;
  domain: string;
  name: string;
  value: number | null;
  unit: string;
  formula: string;
  period: string;
  source_ids: string[];
  input_metric_ids: string[];
  price_basis: string | null;
  status: "ok" | "insufficient_data" | "not_meaningful";
  note: string | null;
}

export interface WorkflowClaim {
  claim_id: string;
  domain: string;
  kind: "observation" | "calculation" | "interpretation";
  statement: string;
  attribution: string;
  metric_ids: string[];
  source_ids: string[];
  quote: string | null;
  period: string | null;
  limitations: string[];
  validation_status: ValidationStatus;
  validation_notes: string[];
  version: number;
  author: string;
  withdrawn?: boolean;
  reviewer_verdicts?: ClaimReviewerVerdict[];
  reviewer_conflict?: boolean;
  frontier?: ClaimFrontier | null;
}

export interface WorkflowPanel {
  domain: string;
  status: PanelStatus;
  headline: string;
  metrics: string[];
  claims: WorkflowClaim[];
  missing_data: string[];
  limitations: string[];
  conflicts: string[];
  model_gap?: string | null;
}

export interface WorkflowSource {
  source_id: string;
  kind: string;
  endpoint: string;
  status: string;
  fetched_at: string;
  available_at: string | null;
  period: string | null;
  sha256: string | null;
  credits: number | null;
  mode: string;
  error: string | null;
}

export interface WorkflowReport {
  run_id: string;
  ticker: string;
  as_of: string;
  horizon: string;
  status: string;
  report_version: number;
  generated_at: string;
  data_mode: string;
  mode: string;
  plan: Record<string, unknown> & {
    analyst_model?: string | null;
    reviewer_model?: string | null;
    reviewer_models?: (string | null)[];
  };
  panels: Record<string, WorkflowPanel>;
  metrics: Record<string, WorkflowMetric>;
  sources: WorkflowSource[];
  synthesis: {
    author: string;
    sections: { text: string; claim_ids: string[]; author?: string }[];
    dropped: { text: string; issues: string[] }[];
    error: string | null;
  };
  validation: { unresolved_claim_ids: string[]; repair_count: number; reviewer_problems?: string[] };
  gate: { status: string; rejected_terms: string[] };
  credits_used: number;
  model_runs: { role: string; model: string; seconds: number; ok: boolean; error?: string | null }[];
  /** Tidak ada pada laporan lama. */
  chronology?: ChronologyAction[];
  frontier?: FrontierRecord | null;
  /** Konfigurasi yang dipakai run ini dan riwayat resume-nya; tidak ada pada laporan lama. */
  runtime?: { bound: RuntimeSnapshot; history: RunConfigHistoryEntry[] } | null;
  node_timings?: { node: string; seconds: number; at: string; config_index?: number | null }[];
  disclaimer: string;
}

export interface WorkflowRun {
  run_id: string;
  ticker: string;
  horizon: string;
  as_of: string;
  data_mode: string;
  status: string;
  job_id: string | null;
  replay_of: string | null;
  error: string | null;
  report_version: number | null;
  created_at: string | null;
}

export interface WorkflowRunResult {
  run_id: string;
  status: string;
  ticker: string;
  as_of: string;
  data_mode: string;
  report_version: number | null;
  credits_used: number;
  panels: Record<string, PanelStatus>;
  unresolved_claims: number;
  model_runs: number;
  frontier_status?: string | null;
  frontier_mode?: string | null;
  frontier_cost_usd_estimate?: number | null;
}

export type RunStatus = "running" | "completed" | "failed";

export interface RunJob {
  id: string;
  kind: "scan" | "scan_retry" | "pipeline" | "workflow";
  run_id?: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  result: (ScanRunResult & Partial<PipelineRunResult> & Partial<WorkflowRunResult> & Partial<ScanRetryResult>) | null;
  error: string | null;
}

export interface Page<T> {
  results: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface EventPage extends Page<ScreenedEventSummary> {
  counts: Partial<Record<VerdictLabel, number>>;
}
