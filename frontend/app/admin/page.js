'use client';

import { useState, useEffect } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TEAL = '#0D7377';

function StatCard({ label, value, sub, status }) {
  const color = status === 'good' ? TEAL : status === 'warn' ? '#b45309' : status === 'bad' ? '#dc2626' : '#111827';
  return (
    <div style={styles.statCard}>
      <div style={{ ...styles.statValue, color }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
      {sub && <div style={styles.statSub}>{sub}</div>}
    </div>
  );
}

export default function AdminPage() {
  const [logs, setLogs]       = useState([]);
  const [stats, setStats]     = useState(null);
  const [filter, setFilter]   = useState('all'); // 'all' | 'success' | 'fail'
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [logsRes, statsRes] = await Promise.all([
        fetch(`${API_URL}/admin/logs?limit=200`),
        fetch(`${API_URL}/admin/stats`),
      ]);
      if (!logsRes.ok || !statsRes.ok) throw new Error('Failed to fetch admin data');
      const logsData  = await logsRes.json();
      const statsData = await statsRes.json();
      setLogs(logsData.logs);
      setStats(statsData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const filtered = logs.filter(l => {
    if (filter === 'success') return l.success === 1;
    if (filter === 'fail')    return l.success === 0;
    return true;
  });

  const formatDate = (iso) => {
    if (!iso) return '—';
    try {
      return new Date(iso + 'Z').toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
    } catch { return iso; }
  };

  return (
    <main style={styles.main}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>◈</span>
          <span style={styles.logoText}>PeopleIQ</span>
          <span style={styles.pageName}>Admin</span>
        </div>
        <div style={styles.headerRight}>
          <button onClick={fetchData} style={styles.refreshBtn} type="button">↺ Refresh</button>
          <a href="/" style={styles.backLink}>← Back to chat</a>
        </div>
      </header>

      {error && (
        <div style={styles.errorBox}>
          <strong>⚠ Could not load admin data.</strong> Is the backend running?
          <br /><span style={{ fontSize: 12, opacity: 0.8 }}>{error}</span>
        </div>
      )}

      {/* Stats row */}
      {stats && (
        <section style={styles.statsRow}>
          <StatCard
            label="Total queries"
            value={stats.total_queries.toLocaleString()}
          />
          <StatCard
            label="Today"
            value={stats.queries_today.toLocaleString()}
          />
          <StatCard
            label="Success rate"
            value={`${stats.success_rate_pct}%`}
            status={stats.success_rate_pct >= 90 ? 'good' : stats.success_rate_pct >= 70 ? 'warn' : 'bad'}
          />
          <StatCard
            label="Avg latency"
            value={`${(stats.avg_latency_ms / 1000).toFixed(1)}s`}
            status={stats.avg_latency_ms < 5000 ? 'good' : stats.avg_latency_ms < 10000 ? 'warn' : 'bad'}
          />
          <StatCard
            label="Avg rows returned"
            value={stats.avg_row_count}
          />
          <StatCard
            label="Failed queries"
            value={stats.fail_count}
            status={stats.fail_count === 0 ? 'good' : stats.fail_count < 5 ? 'warn' : 'bad'}
          />
        </section>
      )}

      {/* Filter tabs */}
      <section style={styles.tableSection}>
        <div style={styles.tableHeader}>
          <div style={styles.filterTabs}>
            {[['all', 'All'], ['success', 'Success'], ['fail', 'Failed']].map(([val, label]) => (
              <button
                key={val}
                onClick={() => setFilter(val)}
                style={{ ...styles.filterTab, ...(filter === val ? styles.filterTabActive : {}) }}
                type="button"
              >
                {label}
                {stats && val === 'all'     && <span style={styles.tabCount}>{stats.total_queries}</span>}
                {stats && val === 'success' && <span style={styles.tabCount}>{stats.success_count}</span>}
                {stats && val === 'fail'    && <span style={styles.tabCount}>{stats.fail_count}</span>}
              </button>
            ))}
          </div>
          <span style={styles.tableCount}>{filtered.length} entries</span>
        </div>

        {loading ? (
          <div style={styles.loadingBox}>Loading query log…</div>
        ) : filtered.length === 0 ? (
          <div style={styles.emptyBox}>
            {logs.length === 0
              ? 'No queries logged yet. Ask a question in the chat to see it here.'
              : 'No entries match this filter.'}
          </div>
        ) : (
          <div style={styles.tableWrapper}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Time</th>
                  <th style={styles.th}>Question</th>
                  <th style={{ ...styles.th, textAlign: 'center' }}>Status</th>
                  <th style={{ ...styles.th, textAlign: 'right' }}>Rows</th>
                  <th style={{ ...styles.th, textAlign: 'right' }}>Latency</th>
                  <th style={styles.th}>SQL / Error</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((log, i) => (
                  <LogRow key={log.id} log={log} i={i} formatDate={formatDate} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <style>{`
        tr:hover td { background: #f9fafb !important; }
      `}</style>
    </main>
  );
}

function LogRow({ log, i, formatDate }) {
  const [expanded, setExpanded] = useState(false);
  const isOdd = i % 2 === 1;

  return (
    <>
      <tr style={{ background: isOdd ? '#fafafa' : '#fff' }}>
        <td style={{ ...styles.td, ...styles.tdMono, whiteSpace: 'nowrap', color: '#6b7280' }}>
          {formatDate(log.asked_at)}
        </td>
        <td style={{ ...styles.td, maxWidth: 280 }}>
          <span style={styles.questionCell}>{log.question}</span>
        </td>
        <td style={{ ...styles.td, textAlign: 'center' }}>
          <span style={log.success === 1 ? styles.pillSuccess : styles.pillFail}>
            {log.success === 1 ? '✓ OK' : '✗ Fail'}
          </span>
        </td>
        <td style={{ ...styles.td, textAlign: 'right', ...styles.tdMono }}>
          {log.row_count ?? '—'}
        </td>
        <td style={{ ...styles.td, textAlign: 'right', ...styles.tdMono, whiteSpace: 'nowrap' }}>
          {log.latency_ms != null ? `${(log.latency_ms / 1000).toFixed(1)}s` : '—'}
        </td>
        <td style={styles.td}>
          {log.success === 1 && log.sql_generated ? (
            <button
              onClick={() => setExpanded(v => !v)}
              style={styles.sqlToggle}
              type="button"
            >
              {expanded ? '▾ Hide SQL' : '▸ View SQL'}
            </button>
          ) : log.error_msg ? (
            <span style={styles.errorMsg}>{log.error_msg.slice(0, 80)}{log.error_msg.length > 80 ? '…' : ''}</span>
          ) : '—'}
        </td>
      </tr>
      {expanded && log.sql_generated && (
        <tr style={{ background: '#f8fafc' }}>
          <td colSpan={6} style={{ padding: '0 16px 12px' }}>
            <pre style={styles.sqlPre}>{log.sql_generated}</pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const styles = {
  main: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '0 24px 48px',
    background: '#fff',
  },
  header: {
    width: '100%', maxWidth: 1000,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '20px 0 16px', marginBottom: 24, borderBottom: '1px solid #e5e7eb',
  },
  logo: { display: 'flex', alignItems: 'center', gap: 8 },
  logoIcon: { fontSize: 22, color: TEAL },
  logoText: { fontWeight: 700, fontSize: 18, color: '#111827', letterSpacing: '-0.3px' },
  pageName: {
    fontSize: 13, fontWeight: 500, color: '#6b7280',
    background: '#f3f4f6', borderRadius: 20, padding: '2px 10px', marginLeft: 4,
  },
  headerRight: { display: 'flex', alignItems: 'center', gap: 14 },
  refreshBtn: {
    fontSize: 13, color: TEAL, background: '#e6f4f5',
    border: '1px solid #b3dfe1', borderRadius: 6,
    padding: '6px 14px', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
  },
  backLink: { fontSize: 13, color: '#6b7280', textDecoration: 'none', fontWeight: 500 },

  errorBox: {
    width: '100%', maxWidth: 1000,
    padding: '14px 18px', background: '#fef2f2',
    border: '1px solid #fecaca', borderRadius: 8,
    fontSize: 14, color: '#7f1d1d', marginBottom: 20,
  },

  /* Stats */
  statsRow: {
    width: '100%', maxWidth: 1000,
    display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)',
    gap: 12, marginBottom: 28,
  },
  statCard: {
    background: '#f9fafb', border: '1px solid #e5e7eb',
    borderRadius: 10, padding: '16px 18px',
    display: 'flex', flexDirection: 'column', gap: 4,
  },
  statValue: { fontSize: 28, fontWeight: 700, lineHeight: 1, color: '#111827' },
  statLabel: { fontSize: 12, color: '#6b7280', fontWeight: 500 },
  statSub:   { fontSize: 11, color: '#9ca3af' },

  /* Table */
  tableSection: { width: '100%', maxWidth: 1000 },
  tableHeader: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 12,
  },
  filterTabs: { display: 'flex', gap: 4 },
  filterTab: {
    fontSize: 13, fontWeight: 500, color: '#6b7280',
    background: 'none', border: '1px solid #e5e7eb',
    borderRadius: 20, padding: '5px 14px', cursor: 'pointer',
    fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 6,
  },
  filterTabActive: {
    color: TEAL, background: '#e6f4f5', borderColor: '#b3dfe1',
  },
  tabCount: {
    fontSize: 11, background: '#e5e7eb', color: '#6b7280',
    borderRadius: 20, padding: '1px 7px', fontWeight: 600,
  },
  tableCount: { fontSize: 13, color: '#9ca3af' },

  tableWrapper: { overflowX: 'auto', border: '1px solid #e5e7eb', borderRadius: 10 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    padding: '10px 16px', textAlign: 'left',
    fontSize: 11, fontWeight: 600, color: '#6b7280',
    letterSpacing: '0.05em', textTransform: 'uppercase',
    background: '#f9fafb', borderBottom: '1px solid #e5e7eb',
  },
  td: {
    padding: '10px 16px', color: '#374151',
    borderBottom: '1px solid #f3f4f6', verticalAlign: 'top',
  },
  tdMono: { fontFamily: '"SFMono-Regular", Consolas, monospace', fontSize: 12 },
  questionCell: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280 },

  pillSuccess: {
    fontSize: 11, fontWeight: 600, color: TEAL,
    background: '#e6f4f5', border: '1px solid #b3dfe1',
    borderRadius: 20, padding: '2px 9px',
  },
  pillFail: {
    fontSize: 11, fontWeight: 600, color: '#dc2626',
    background: '#fef2f2', border: '1px solid #fecaca',
    borderRadius: 20, padding: '2px 9px',
  },
  sqlToggle: {
    fontSize: 12, color: TEAL, background: 'none', border: 'none',
    cursor: 'pointer', fontFamily: 'inherit', padding: 0, fontWeight: 500,
  },
  sqlPre: {
    margin: 0, padding: '12px 16px', background: '#f1f5f9',
    border: '1px solid #e2e8f0', borderRadius: 6,
    fontSize: 12, color: '#374151',
    fontFamily: '"SFMono-Regular", Consolas, monospace',
    overflowX: 'auto', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  },
  errorMsg: { fontSize: 12, color: '#dc2626', fontFamily: 'monospace' },

  loadingBox: { padding: '32px', textAlign: 'center', color: '#6b7280', fontSize: 14 },
  emptyBox:   { padding: '32px', textAlign: 'center', color: '#9ca3af', fontSize: 14 },
};
