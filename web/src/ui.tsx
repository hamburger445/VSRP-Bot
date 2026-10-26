import { useMemo, useState, type ReactNode } from "react";

export function StatusBadge({ value }: { value?: string | null }) {
  const v = (value || "unknown").toLowerCase().replace(/\s+/g, "_");
  return <span className={`badge badge-${v}`}>{value || "—"}</span>;
}

export function PageHeader({
  kicker,
  title,
  actions,
}: {
  kicker?: string;
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        {kicker && <p className="kicker">{kicker}</p>}
        <h2>{title}</h2>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="muted tiny">{hint}</div>}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      {(title || actions) && (
        <header className="panel-head">
          <h3>{title}</h3>
          {actions}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <p className="muted">{body}</p>
    </div>
  );
}

export function DataTable({
  rows,
  cols,
  empty,
  searchable,
}: {
  rows: any[];
  cols: [string, string][];
  empty: string;
  searchable?: boolean;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<[string, boolean] | null>(null);
  const filtered = useMemo(() => {
    let list = rows || [];
    if (q.trim()) {
      const needle = q.toLowerCase();
      list = list.filter((r) => cols.some(([k]) => String(r?.[k] ?? "").toLowerCase().includes(needle)));
    }
    if (sort) {
      const [key, desc] = sort;
      list = [...list].sort((a, b) => {
        const av = a?.[key];
        const bv = b?.[key];
        if (av == null) return 1;
        if (bv == null) return -1;
        return desc ? String(bv).localeCompare(String(av), undefined, { numeric: true }) : String(av).localeCompare(String(bv), undefined, { numeric: true });
      });
    }
    return list;
  }, [rows, cols, q, sort]);
  return (
    <div>
      {searchable && (
        <div className="table-tools">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter table…" aria-label="Filter table" />
        </div>
      )}
      {!filtered.length ? (
        <p className="muted empty-inline">{empty}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {cols.map(([k, l]) => (
                  <th key={k}>
                    <button
                      type="button"
                      className="th-btn"
                      onClick={() => setSort((s) => (s && s[0] === k ? [k, !s[1]] : [k, false]))}
                    >
                      {l}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={r.id ?? i}>
                  {cols.map(([k]) => (
                    <td key={k}>
                      {k === "status" || k === "priority" || k === "discipline" ? (
                        <StatusBadge value={String(r[k] ?? "")} />
                      ) : (
                        String(r[k] ?? "—")
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}
