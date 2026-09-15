import type { AuditLogEntry } from "../types";

export function AuditTrail({ entries }: { entries: AuditLogEntry[] }) {
  return (
    <div className="audit-trail">
      <h2 className="audit-trail__title">Audit Trail</h2>
      <p className="audit-trail__subtitle">Setiap keputusan SENSE / VALIDATE / GATE tercatat, tidak ada yang tersembunyi.</p>
      <table className="audit-trail__table">
        <thead>
          <tr>
            <th>Waktu</th>
            <th>Stage</th>
            <th>Ticker</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id}>
              <td>{new Date(entry.created_at).toLocaleTimeString()}</td>
              <td>
                <span className={`audit-trail__stage audit-trail__stage--${entry.stage}`}>{entry.stage}</span>
              </td>
              <td>{entry.ticker}</td>
              <td className="audit-trail__detail">{JSON.stringify(entry.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
