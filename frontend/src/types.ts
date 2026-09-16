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

export interface ScreenedEventDetail {
  research?: {
    status: string;
    extraction_attempts: number;
    case_id: string | null;
    issues: string[];
    evidence: { id: string; kind: string; url: string; title: string; retrieved_at: string }[];
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
