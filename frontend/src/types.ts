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
  plan: Record<string, unknown> & { analyst_model?: string | null; reviewer_model?: string | null };
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
}

export type RunStatus = "running" | "completed" | "failed";

export interface RunJob {
  id: string;
  kind: "scan" | "pipeline" | "workflow";
  run_id?: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  result: (ScanRunResult & Partial<PipelineRunResult> & Partial<WorkflowRunResult>) | null;
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
