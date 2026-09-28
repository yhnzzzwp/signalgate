import {
  FRONTIER_MODE_TEXT,
  FRONTIER_RULE_TEXT,
  FRONTIER_STATUS_TEXT,
  FRONTIER_TRIGGER_TEXT,
  LABEL_TEXT,
} from "../labels";
import type { FrontierCall, FrontierRecord, VerdictLabel } from "../types";
import { ChronologyView } from "./ChronologyView";

const ATTENTION = new Set(["failed", "unavailable", "budget_exhausted", "offline_cache_miss", "partial"]);

function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "tidak diketahui";
  return `≈ $${value.toFixed(value < 0.01 ? 5 : 4)}`;
}

function CallRow({ call }: { call: FrontierCall }) {
  const usage = call.usage ?? {};
  return (
    <li>
      <strong>{call.step === "independent" ? "Pembacaan independen" : "Langkah kedua"}</strong> ·{" "}
      {call.cached ? "dari cache (tanpa biaya baru)" : (FRONTIER_STATUS_TEXT[call.status ?? ""] ?? call.status)}
      {call.attempts > 1 && ` · ${call.attempts} percobaan`}
      {!call.cached && call.seconds > 0 && ` · ${call.seconds}s`}
      {(usage.prompt_tokens ?? 0) > 0 &&
        ` · token masuk ${usage.prompt_tokens}, keluar ${usage.completion_tokens ?? 0}` +
          (usage.reasoning_tokens ? ` (penalaran ${usage.reasoning_tokens})` : "")}
      {!call.cached && ` · biaya ${usd(call.usage_known ? call.cost_usd_estimate : null)} (estimasi)`}
      {call.error && <span className="event-card__source-meta"> — {call.error}</span>}
      {call.cost_note && <span className="event-card__source-meta"> — {call.cost_note}</span>}
    </li>
  );
}

/**
 * Audit reviewer frontier. Hasil shadow ditandai jelas sebagai pembanding yang tidak dipakai keputusan;
 * hasil escalation hanya berlaku lewat aturan rekonsiliasi kode, dan aturannya ditampilkan per klaim.
 */
export function FrontierAudit({ record }: { record: FrontierRecord | null | undefined }) {
  if (!record || !record.enabled) return null;
  const shadow = record.mode !== "escalation";
  const totals = record.totals;
  const differences = record.differences ?? [];
  const reconciliation = record.reconciliation ?? [];
  const localLabel = record.local_outcome?.label as VerdictLabel | undefined;
  const reconciledLabel = record.reconciled_outcome?.label as VerdictLabel | undefined;
  const timelineChanged =
    JSON.stringify(record.timeline_conflicts?.local_messages ?? []) !==
    JSON.stringify(record.timeline_conflicts?.with_frontier_messages ?? []);

  return (
    <details className={`event-card__review frontier ${shadow ? "frontier--shadow" : ""}`} open={ATTENTION.has(record.status)}>
      <summary>
        Reviewer frontier {record.model_version ?? record.model ?? ""} ·{" "}
        {FRONTIER_STATUS_TEXT[record.status] ?? record.status} · {shadow ? "shadow" : "escalation"}
        {record.applied ? " · dipakai keputusan" : shadow ? " · tidak dipakai keputusan" : ""}
      </summary>
      <p className="event-card__source-meta">{FRONTIER_MODE_TEXT[record.mode ?? "shadow"] ?? record.mode}</p>
      {record.reuse_note && <p className="event-card__source-meta">{record.reuse_note}</p>}
      {record.message && <p className="frontier__message">{record.message}</p>}

      {(record.triggers ?? []).length > 0 && (
        <>
          <h5>Alasan eskalasi</h5>
          <ul className="event-card__issues">
            {(record.triggers ?? []).map((trigger, index) => (
              <li key={`${trigger.claim_id}-${trigger.reason}-${index}`}>
                {trigger.claim_id ? <code>{trigger.claim_id}</code> : "label"}:{" "}
                {FRONTIER_TRIGGER_TEXT[trigger.reason] ?? trigger.reason}
              </li>
            ))}
          </ul>
        </>
      )}

      {localLabel && reconciledLabel && (
        <p className="event-card__source-meta">
          Hasil lokal: {LABEL_TEXT[localLabel] ?? localLabel} ({record.local_outcome?.status}) · dengan aturan
          rekonsiliasi: {LABEL_TEXT[reconciledLabel] ?? reconciledLabel} ({record.reconciled_outcome?.status})
          {shadow && record.decision_changed && " — hanya simulasi, tidak diterbitkan"}
        </p>
      )}

      {reconciliation.length > 0 && (
        <>
          <h5>Perbedaan penilaian {shadow ? "(shadow — tidak mengubah hasil)" : ""}</h5>
          <table className="workflow__metrics">
            <thead>
              <tr>
                <th>Klaim</th>
                <th>Pembanding lokal</th>
                <th>Frontier</th>
                <th>Aturan kode</th>
                <th>{shadow ? "Akan menjadi" : "Hasil"}</th>
              </tr>
            </thead>
            <tbody>
              {reconciliation.map((item) => (
                <tr
                  key={item.claim_id}
                  className={differences.some((diff) => diff.claim_id === item.claim_id) ? "chronology__row--conflict" : undefined}
                >
                  <td>
                    <code>{item.claim_id}</code>
                  </td>
                  <td>{item.local_statuses.map((status) => status ?? "–").join(" / ")}</td>
                  <td>{item.frontier_status ?? "–"}</td>
                  <td title={item.note}>{FRONTIER_RULE_TEXT[item.rule] ?? item.rule}</td>
                  <td>{item.final_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {(record.evidence_reading ?? []).length > 0 && (
        <details className="event-card__review">
          <summary>Pembacaan bukti frontier ({record.evidence_reading?.length})</summary>
          <ul className="event-card__issues">
            {(record.evidence_reading ?? []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}

      {timelineChanged && (record.chronology ?? []).length > 0 && (
        <ChronologyView
          actions={record.chronology ?? []}
          title={shadow ? "Kronologi bila entri frontier dipakai (shadow)" : "Kronologi dengan entri frontier terverifikasi"}
        />
      )}

      {(record.calls ?? []).length > 0 && (
        <>
          <h5>Panggilan</h5>
          <ul className="event-card__issues">
            {(record.calls ?? []).map((call, index) => (
              <CallRow key={`${call.step}-${index}`} call={call} />
            ))}
          </ul>
        </>
      )}
      {totals && (
        <p className="event-card__source-meta">
          Total run ini: {totals.calls} panggilan ({totals.attempts} percobaan, {totals.cache_hits} dari cache) · token
          masuk {totals.prompt_tokens}, keluar {totals.completion_tokens} · biaya{" "}
          {totals.usage_unknown
            ? `tidak diketahui — bagian yang diketahui ${usd(totals.cost_usd_known_part ?? 0)}, sisanya dihitung ke budget sebesar reservasi`
            : usd(totals.cost_usd_estimate)}{" "}
          — estimasi USD harga {totals.price_version}, terpisah dari kredit Sectors.
          {totals.usage_partial && " Token yang tampil hanya bagian yang diketahui."}
        </p>
      )}
      {totals?.overshoot && (
        <p className="frontier__message">
          Pemakaian aktual melebihi reservasi; panggilan berikutnya pada run ini dihentikan. Batas biaya berbasis
          estimasi, bukan jaminan nominal.
        </p>
      )}
      {typeof record.budget?.total_cost_usd_charged === "number" && (
        <p className="event-card__source-meta">
          Budget frontier terpakai: hari ini {usd(record.budget.day_cost_usd_charged as number)} dari $
          {String(record.budget.max_cost_usd_per_day)} · total {usd(record.budget.total_cost_usd_charged)}
          {typeof record.budget.max_cost_usd_total === "number" && ` dari $${record.budget.max_cost_usd_total}`} (estimasi,
          termasuk reservasi yang usage-nya tidak diketahui).
        </p>
      )}
    </details>
  );
}
