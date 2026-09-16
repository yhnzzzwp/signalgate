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
  processed: number;
  provider: string | null;
  articles_checked: number;
  listing_pages_fetched: number;
  failed_sources: { url: string; error: string }[];
  coverage_note: string;
}

export interface PipelineRunResult {
  screened_count: number;
  provider: string;
}

export type RunStatus = "running" | "completed" | "failed";

export interface RunJob {
  id: string;
  kind: "scan" | "pipeline";
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  result: (ScanRunResult & Partial<PipelineRunResult>) | null;
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
