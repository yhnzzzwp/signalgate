import { LABEL_TEXT } from "../labels";
import type { VerdictLabel } from "../types";

export function VerdictBadge({ label, confidence }: { label: VerdictLabel; confidence: number }) {
  return (
    <span className={`verdict-badge verdict-badge--${label}`}>
      {LABEL_TEXT[label]}
      <span className="verdict-badge__confidence" title="Skor heuristik kekuatan sinyal; bukan probabilitas benar atau prediksi return.">Skor {Math.round(confidence * 100)}/100</span>
    </span>
  );
}
