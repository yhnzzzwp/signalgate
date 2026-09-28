import { TIMELINE_METRIC_TEXT, TIMELINE_STAGE_TEXT } from "../labels";
import type { ChronologyAction, ChronologyEntry } from "../types";

function formatAmount(value: number | null, unit: string): string {
  if (value === null) return "belum terselesaikan";
  const number = (n: number, digits = 2) => n.toLocaleString("id-ID", { maximumFractionDigits: digits });
  if (unit === "%") return `${number(value)}%`;
  if (unit === "IDR" || unit === "IDR/saham") {
    const size = Math.abs(value);
    const suffix = unit === "IDR/saham" ? " per saham" : "";
    if (size >= 1e12) return `Rp${number(value / 1e12)} triliun${suffix}`;
    if (size >= 1e9) return `Rp${number(value / 1e9)} miliar${suffix}`;
    if (size >= 1e6) return `Rp${number(value / 1e6)} juta${suffix}`;
    return `Rp${number(value, 0)}${suffix}`;
  }
  if (Math.abs(value) >= 1e6) return `${number(value / 1e6)} juta saham`;
  return `${number(value, 0)} saham`;
}

function EntryRow({ entry, conflicted }: { entry: ChronologyEntry; conflicted: boolean }) {
  return (
    <tr className={conflicted ? "chronology__row--conflict" : undefined}>
      <td>{entry.event_date ?? <span className="event-card__source-meta">tidak tertulis</span>}</td>
      <td>{entry.published_at ? entry.published_at.slice(0, 10) : "-"}</td>
      <td title={entry.stage_basis}>{TIMELINE_STAGE_TEXT[entry.stage] ?? entry.stage}</td>
      <td>
        <strong>{entry.value_text}</strong>
        <span className="event-card__source-meta"> · {TIMELINE_METRIC_TEXT[entry.metric] ?? entry.metric}</span>
        {entry.revises_entry_ids.length > 0 && (
          <span className="event-card__source-meta"> · merevisi {entry.revises_entry_ids.join(", ")}</span>
        )}
      </td>
      <td>
        <code>{entry.source_id}</code>
        <span className="event-card__source-meta">
          {" "}
          · {entry.origin === "frontier" ? "dibaca frontier" : entry.claim_id ?? "klaim lokal"}
        </span>
      </td>
    </tr>
  );
}

/**
 * Kronologi angka aksi korporasi. Tanggal kejadian (hanya bila tertulis di sumber) dipisah dari tanggal
 * terbit, dan angka yang bertentangan tanpa bukti revisi ditampilkan semuanya, bukan dipilih yang terbaru.
 */
export function ChronologyView({ actions, title }: { actions: ChronologyAction[]; title?: string }) {
  if (actions.length === 0) return null;
  return (
    <section className="chronology">
      <h4>{title ?? "Kronologi aksi korporasi"}</h4>
      {actions.map((action) => {
        const conflicted = new Set(action.conflicts.flatMap((conflict) => conflict.entry_ids));
        return (
          <article key={action.action_key} className="chronology__action">
            <header className="workflow__metric-head">
              <span>
                {action.action_type.replace(/_/g, " ")}
                {action.action_ref ? ` · ${action.action_ref}` : ""}
              </span>
              <span className="event-card__source-meta">{action.identity_note}</span>
            </header>
            <ul className="chronology__metrics">
              {Object.entries(action.metrics).map(([metric, summary]) => (
                <li key={metric}>
                  {TIMELINE_METRIC_TEXT[metric] ?? metric}: ketentuan{" "}
                  <strong>{formatAmount(summary.terms_value, summary.unit)}</strong>
                  {summary.realized_value !== null && (
                    <>
                      {" "}
                      · realisasi <strong>{formatAmount(summary.realized_value, summary.unit)}</strong>
                    </>
                  )}
                  <span className="event-card__source-meta"> — {summary.terms_note}</span>
                </li>
              ))}
            </ul>
            {action.conflicts.length > 0 && (
              <ul className="event-card__issues">
                {action.conflicts.map((conflict) => (
                  <li key={`${conflict.metric}-${conflict.stage_class}`}>
                    Konflik {TIMELINE_METRIC_TEXT[conflict.metric] ?? conflict.metric} ({conflict.stage_class}):{" "}
                    {conflict.note}.
                  </li>
                ))}
              </ul>
            )}
            <table className="workflow__metrics chronology__table">
              <thead>
                <tr>
                  <th>Tanggal kejadian</th>
                  <th>Terbit</th>
                  <th>Tahap</th>
                  <th>Angka</th>
                  <th>Sumber</th>
                </tr>
              </thead>
              <tbody>
                {action.entries.map((entry) => (
                  <EntryRow key={entry.entry_id} entry={entry} conflicted={conflicted.has(entry.entry_id)} />
                ))}
              </tbody>
            </table>
          </article>
        );
      })}
    </section>
  );
}
