import type { VerdictLabel } from "../types";

const LABEL_TEXT: Record<VerdictLabel, string> = {
  growth_catalyst: "Growth Catalyst",
  structural_red_flag: "Structural Red Flag",
  inconclusive: "Inconclusive",
};

export function VerdictBadge({ label, confidence }: { label: VerdictLabel; confidence: number }) {
  return (
    <span className={`verdict-badge verdict-badge--${label}`}>
      {LABEL_TEXT[label]}
      <span className="verdict-badge__confidence">{Math.round(confidence * 100)}%</span>
    </span>
  );
}
