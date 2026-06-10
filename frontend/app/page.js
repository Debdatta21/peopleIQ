'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import * as d3 from 'd3';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const EXAMPLE_QUESTIONS = [
  'What is our current headcount?',
  'What is our rolling 12-month turnover rate?',
  'Which properties have the most open requisitions?',
  'What are the top reasons employees are leaving?',
  'How long does it take us to fill a role on average?',
  'What percentage of new hires are still with us after 12 months?',
];

// ── Data Table component ──────────────────────────────────────────────────────
function DataTable({ rows }) {
  if (!rows || rows.length === 0) return null;
  const cols = Object.keys(rows[0]);
  return (
    <div style={styles.tableWrapper}>
      <table style={styles.dataTable}>
        <thead>
          <tr>
            {cols.map(c => (
              <th key={c} style={styles.dataTh}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ background: i % 2 === 0 ? '#fff' : '#f9fafb' }}>
              {cols.map(c => (
                <td key={c} style={styles.dataTd}>
                  {row[c] === null || row[c] === undefined ? '—' : String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── D3 Chart component ────────────────────────────────────────────────────────
const CHART_COLORS = ['#0D7377', '#f59e0b', '#6366f1', '#ef4444', '#10b981'];

function D3Chart({ data }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const el = containerRef.current;
    d3.select(el).selectAll('*').remove();

    const { type, labels, series } = data;
    const M = { top: 24, right: 24, bottom: 48, left: 56 };
    const totalW = el.clientWidth || 660;
    const totalH = series.length > 1 ? 280 : 260;
    const W = totalW - M.left - M.right;
    const H = totalH - M.top - M.bottom - (series.length > 1 ? 24 : 0);

    const svg = d3.select(el)
      .append('svg')
      .attr('width', totalW)
      .attr('height', totalH)
      .attr('xmlns', 'http://www.w3.org/2000/svg');

    const g = svg.append('g').attr('transform', `translate(${M.left},${M.top})`);

    const allVals = series.flatMap(s => s.values);
    const yMax = (d3.max(allVals) || 1) * 1.12;

    const y = d3.scaleLinear().domain([0, yMax]).range([H, 0]).nice();

    // Y axis with grid
    g.append('g')
      .call(d3.axisLeft(y)
        .ticks(4)
        .tickSize(-W)
        .tickFormat(v => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v)
      )
      .call(ax => ax.select('.domain').remove())
      .call(ax => ax.selectAll('.tick line').attr('stroke', '#e5e7eb').attr('stroke-dasharray', '3,3'))
      .call(ax => ax.selectAll('.tick text').attr('fill', '#6b7280').attr('font-size', '11px'));

    if (type === 'bar') {
      const x = d3.scaleBand().domain(labels).range([0, W]).paddingInner(0.28).paddingOuter(0.1);
      const xSub = d3.scaleBand().domain(series.map(s => s.name)).range([0, x.bandwidth()]).padding(0.06);

      series.forEach((s, si) => {
        const color = CHART_COLORS[si % CHART_COLORS.length];
        g.selectAll(null)
          .data(labels)
          .enter().append('rect')
          .attr('x', d => x(d) + xSub(s.name))
          .attr('y', (d, i) => y(s.values[i]))
          .attr('width', xSub.bandwidth())
          .attr('height', (d, i) => Math.max(0, H - y(s.values[i])))
          .attr('fill', color)
          .attr('rx', 3);
      });

      g.append('g')
        .attr('transform', `translate(0,${H})`)
        .call(d3.axisBottom(x).tickSize(0))
        .call(ax => ax.select('.domain').attr('stroke', '#e5e7eb'))
        .call(ax => ax.selectAll('.tick text')
          .attr('fill', '#6b7280').attr('font-size', '11px')
          .text(d => String(d).length > 12 ? String(d).slice(0, 12) + '…' : d));

    } else {
      // Line chart
      const x = d3.scalePoint().domain(labels).range([0, W]).padding(0.15);

      series.forEach((s, si) => {
        const color = CHART_COLORS[si % CHART_COLORS.length];

        // Area fill for single series
        if (series.length === 1) {
          const area = d3.area()
            .x((d, i) => x(labels[i]))
            .y0(H).y1(d => y(d))
            .curve(d3.curveMonotoneX);
          g.append('path').datum(s.values)
            .attr('fill', color).attr('opacity', 0.08)
            .attr('d', area);
        }

        // Line
        const line = d3.line()
          .x((d, i) => x(labels[i])).y(d => y(d))
          .curve(d3.curveMonotoneX);
        g.append('path').datum(s.values)
          .attr('fill', 'none').attr('stroke', color)
          .attr('stroke-width', 2.5).attr('d', line);

        // Dots
        g.selectAll(null).data(s.values).enter().append('circle')
          .attr('cx', (d, i) => x(labels[i])).attr('cy', d => y(d))
          .attr('r', 4).attr('fill', color)
          .attr('stroke', '#fff').attr('stroke-width', 2);
      });

      g.append('g')
        .attr('transform', `translate(0,${H})`)
        .call(d3.axisBottom(x).tickSize(0))
        .call(ax => ax.select('.domain').attr('stroke', '#e5e7eb'))
        .call(ax => ax.selectAll('.tick text')
          .attr('fill', '#6b7280').attr('font-size', '11px'));
    }

    // Legend for multi-series
    if (series.length > 1) {
      const legendY = totalH - 20;
      const legend = svg.append('g').attr('transform', `translate(${M.left},${legendY})`);
      series.forEach((s, si) => {
        const lx = si * 150;
        legend.append('rect').attr('x', lx).attr('y', 0)
          .attr('width', 10).attr('height', 10).attr('rx', 2)
          .attr('fill', CHART_COLORS[si % CHART_COLORS.length]);
        legend.append('text').attr('x', lx + 14).attr('y', 9)
          .attr('font-size', '11px').attr('fill', '#6b7280')
          .text(s.name.length > 18 ? s.name.slice(0, 18) + '…' : s.name);
      });
    }
  }, [data]);

  const downloadSVG = () => {
    const svgEl = containerRef.current?.querySelector('svg');
    if (!svgEl) return;
    const serializer = new XMLSerializer();
    const svgStr = serializer.serializeToString(svgEl);
    const blob = new Blob([svgStr], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `peopleiq-chart-${Date.now()}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={styles.chartWrapper}>
      <div ref={containerRef} style={{ width: '100%' }} />
      <button onClick={downloadSVG} style={styles.downloadBtn} type="button">
        ↓ Download chart
      </button>
    </div>
  );
}

// ── Single message bubble ─────────────────────────────────────────────────────
function MessageBubble({ msg }) {
  const [sqlExpanded, setSqlExpanded] = useState(false);

  return (
    <div style={styles.messageGroup}>
      {/* User question */}
      <div style={styles.userBubble}>
        <span style={styles.userAvatar}>You</span>
        <div style={styles.userText}>{msg.question}</div>
      </div>

      {/* AI response */}
      <div style={styles.aiBubble}>
        <span style={styles.aiAvatar}>◈</span>
        <div style={styles.aiContent}>
          {msg.status === 'pending' && (
            <div style={styles.loadingDots}>
              <span style={{ ...styles.dot, animationDelay: '0ms' }} />
              <span style={{ ...styles.dot, animationDelay: '160ms' }} />
              <span style={{ ...styles.dot, animationDelay: '320ms' }} />
            </div>
          )}

          {msg.status === 'error' && (
            <div style={styles.errorInline}>
              <span style={{ color: '#dc2626', marginRight: 6 }}>⚠</span>
              <span>{msg.error}</span>
              {msg.retryable && msg.onRetry && (
                <button onClick={msg.onRetry} style={styles.retryBtn} type="button">
                  Try again
                </button>
              )}
            </div>
          )}

          {msg.status === 'done' && (
            <>
              {(() => {
                const parts = msg.answer.split(/\nData sources:/i);
                return (
                  <>
                    <p style={styles.answerText}>{parts[0].trim()}</p>
                    {parts[1] && (
                      <p style={styles.sources}>
                        <span style={styles.sourcesLabel}>Data sources:</span>{parts[1].trim()}
                      </p>
                    )}
                  </>
                );
              })()}

              {/* Visualisation — chart, table, or KPI based on output_type */}
              {msg.outputType === 'table' && msg.rows && (
                <div style={{ marginTop: 14, borderTop: '1px solid #f3f4f6', paddingTop: 14 }}>
                  <DataTable rows={msg.rows} />
                </div>
              )}
              {msg.outputType === 'chart' && msg.chartData && <D3Chart data={msg.chartData} />}
              {msg.outputType === 'kpi' && msg.rowCount > 0 && null /* answer text is sufficient */}

              {/* Meta row */}
              <div style={styles.metaRow}>
                {msg.rowCount !== null && (
                  <span style={styles.rowPill}>
                    {msg.rowCount} {msg.rowCount === 1 ? 'row' : 'rows'} returned
                  </span>
                )}
              </div>

              {/* Collapsible SQL */}
              {msg.sql && (
                <div style={styles.sqlWrapper}>
                  <button
                    onClick={() => setSqlExpanded(v => !v)}
                    style={styles.sqlToggle}
                    type="button"
                  >
                    <span style={styles.sqlToggleIcon}>{sqlExpanded ? '▾' : '▸'}</span>
                    How this was calculated
                  </button>
                  {sqlExpanded && (
                    <pre style={styles.sqlBlock}><code>{msg.sql}</code></pre>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function Home() {
  const [question, setQuestion]             = useState('');
  const [messages, setMessages]             = useState([]);
  const [loading, setLoading]               = useState(false);
  const [summary, setSummary]               = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const inputRef                            = useRef(null);
  const threadEndRef                        = useRef(null);

  // Wake backend + load workforce brief on mount
  useEffect(() => {
    // Ping /health first to wake Render if it's sleeping, then fetch summary
    fetch(`${API_URL}/health`).catch(() => {});
    fetch(`${API_URL}/summary`)
      .then(r => r.json())
      .then(d => { setSummary(d.metrics); setSummaryLoading(false); })
      .catch(() => setSummaryLoading(false));
  }, []);

  // Auto-scroll to bottom when new message arrives
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleChip = (q) => {
    setQuestion(q);
    inputRef.current?.focus();
  };

  const submitQuestion = useCallback(async (q, retryId = null) => {
    if (!q || loading) return;

    const history = messages
      .filter(m => m.status === 'done')
      .slice(-3)
      .map(m => ({ question: m.question, answer: m.answer }));

    const id = retryId || Date.now();
    if (!retryId) {
      setMessages(prev => [...prev, { id, question: q, status: 'pending' }]);
    } else {
      setMessages(prev => prev.map(m => m.id === id ? { ...m, status: 'pending', error: null } : m));
    }
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, history }),
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const is429 = res.status === 429;
        const errMsg = is429
          ? 'Groq is rate-limited right now. Wait 30–60 seconds, then tap "Try again".'
          : (detail?.detail || `Something went wrong (error ${res.status}). Try rephrasing the question.`);
        setMessages(prev => prev.map(m =>
          m.id === id ? {
            ...m, status: 'error', error: errMsg, retryable: is429,
            onRetry: is429 ? () => submitQuestion(q, id) : null,
          } : m
        ));
        return;
      }

      const data = await res.json();
      setMessages(prev => prev.map(m =>
        m.id === id ? {
          ...m,
          status: 'done',
          answer: data.answer,
          sql: data.sql,
          rowCount: data.row_count,
          chartData: data.chart_data || null,
          outputType: data.output_type || 'chart',
          rows: data.rows || null,
        } : m
      ));
    } catch (err) {
      const errMsg = 'Could not reach the backend. Make sure it\'s running and try again.';
      setMessages(prev => prev.map(m =>
        m.id === id ? { ...m, status: 'error', error: errMsg, retryable: true,
          onRetry: () => submitQuestion(q, id) } : m
      ));
    } finally {
      setLoading(false);
    }
  }, [messages, loading]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;
    setQuestion('');
    await submitQuestion(q);
  };

  return (
    <main style={styles.main}>
      {/* ── Header ── */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>◈</span>
          <span style={styles.logoText}>PeopleIQ</span>
        </div>
        <div style={styles.headerRight}>
          <a href="/admin" style={styles.adminLink}>Admin ↗</a>
          <span style={styles.badge}>PoC</span>
        </div>
      </header>

      {/* ── Synthetic data disclaimer ── */}
      <div style={styles.disclaimer}>
        <span style={styles.disclaimerIcon}>⚠</span>
        <span>
          <strong>Demo only — synthetic data.</strong> All employees, names, and figures are
          computer-generated and do not represent any real individuals or organizations.
          No private or confidential information is stored or accessible.
        </span>
      </div>

      {/* ── Hero: hidden once chat starts ── */}
      {messages.length === 0 && (
        <section style={styles.hero}>
          <h1 style={styles.h1}>Ask anything about your workforce.</h1>
          <p style={styles.subheadline}>
            Type a plain-English question. Get instant answers from your HR data — headcount, attrition, open roles, exit interviews, and more.
          </p>
        </section>
      )}

      {/* ── Workforce brief: always visible ── */}
      <section style={styles.briefSection}>
            <div style={styles.briefHeader}>
              <span style={styles.briefLabel}>Workforce brief</span>
              <span style={styles.briefDate}>
                {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
              </span>
            </div>

            {summaryLoading ? (
              <div style={styles.briefSkeleton}>
                {[...Array(6)].map((_, i) => <div key={i} style={styles.skeletonCard} />)}
              </div>
            ) : summary ? (
              <div style={styles.briefGrid}>
                {summary.map(m => (
                  <div key={m.key} style={{ ...styles.hCard, ...styles[`hCard_${m.status}`] }}>
                    <div style={{ ...styles.hStatus, ...styles[`hStatus_${m.status}`] }}>
                      {m.status === 'good' ? 'All clear' : m.status === 'watch' ? 'Watch' : 'Needs attention'}
                    </div>
                    <div style={styles.hHeadline}>{m.headline}</div>
                    <div style={styles.hDetail}>{m.detail}</div>
                    <button
                      style={styles.hBtn}
                      onClick={() => { setQuestion(m.question); inputRef.current?.focus(); }}
                      type="button"
                    >
                      Ask about this ↗
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
      </section>

      {/* ── Chat thread ── */}
      {messages.length > 0 && (
        <section style={styles.thread}>
          {messages.map(msg => <MessageBubble key={msg.id} msg={msg} />)}
          <div ref={threadEndRef} />
        </section>
      )}

      {/* ── Input bar — sticky at bottom ── */}
      <section style={styles.inputSection}>
        {messages.length === 0 && (
          <div style={styles.chips}>
            {EXAMPLE_QUESTIONS.map(q => (
              <button key={q} onClick={() => handleChip(q)} style={styles.chip} type="button">{q}</button>
            ))}
          </div>
        )}

        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.inputRow}>
            <input
              ref={inputRef}
              type="text"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              placeholder={messages.length === 0
                ? 'e.g. What is our attrition rate this quarter?'
                : 'Ask a follow-up…'}
              style={styles.input}
              disabled={loading}
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="submit"
              disabled={loading || !question.trim()}
              style={{ ...styles.button, ...(loading || !question.trim() ? styles.buttonDisabled : {}) }}
            >
              {loading ? <span style={styles.spinner} /> : 'Ask'}
            </button>
          </div>
        </form>
      </section>

      {/* ── Footer ── */}
      <footer style={styles.footer}>
        <p style={styles.footerText}>
          PeopleIQ · Built by Debdatta Gupta ·{' '}
          <a
            href="https://github.com/Debdatta21/peopleIQ"
            target="_blank"
            rel="noopener noreferrer"
            style={styles.footerLink}
          >
            GitHub
          </a>
        </p>
      </footer>

      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40%            { opacity: 1;   transform: scale(1);   }
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        button:hover:not(:disabled) { opacity: 0.88; }
      `}</style>
    </main>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const TEAL = '#0D7377';

const styles = {
  main: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '0 24px 48px',
    background: '#ffffff',
  },

  /* Header */
  header: {
    width: '100%',
    maxWidth: 760,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '20px 0 16px',
    marginBottom: 20,
    borderBottom: '1px solid #e5e7eb',
  },
  logo: { display: 'flex', alignItems: 'center', gap: 8 },
  logoIcon: { fontSize: 22, color: TEAL },
  logoText: { fontWeight: 700, fontSize: 18, color: '#111827', letterSpacing: '-0.3px' },
  headerRight: { display: 'flex', alignItems: 'center', gap: 16 },
  adminLink: { fontSize: 13, color: '#6b7280', textDecoration: 'none', fontWeight: 500 },
  badge: {
    fontSize: 12, fontWeight: 500, color: TEAL,
    background: '#e6f4f5', border: '1px solid #b3dfe1',
    borderRadius: 20, padding: '3px 10px',
  },

  /* Disclaimer */
  disclaimer: {
    width: '100%', maxWidth: 760,
    display: 'flex', alignItems: 'flex-start', gap: 10,
    padding: '10px 16px', background: '#fffbeb',
    border: '1px solid #fcd34d', borderRadius: 8,
    fontSize: 13, color: '#92400e', lineHeight: 1.5, marginBottom: 28,
  },
  disclaimerIcon: { flexShrink: 0, marginTop: 1, fontSize: 14 },

  /* Hero */
  hero: { textAlign: 'center', maxWidth: 620, marginBottom: 36 },
  h1: {
    fontSize: 'clamp(26px, 5vw, 40px)', fontWeight: 800,
    color: '#111827', letterSpacing: '-0.8px', lineHeight: 1.2, marginBottom: 10,
  },
  subheadline: { fontSize: 17, color: '#6b7280', fontWeight: 400 },

  /* Workforce brief */
  briefSection: { width: '100%', maxWidth: 760, marginBottom: 28 },
  briefHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  briefLabel: { fontSize: 11, fontWeight: 600, color: '#6b7280', letterSpacing: '0.07em', textTransform: 'uppercase' },
  briefDate: { fontSize: 11, color: '#9ca3af' },
  briefGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 },
  briefSkeleton: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 },
  skeletonCard: { height: 96, background: '#f3f4f6', borderRadius: 10, animation: 'pulse 1.4s ease-in-out infinite' },
  hCard: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, padding: '14px 16px 12px', borderLeft: '3px solid #e5e7eb' },
  hCard_good:  { borderLeftColor: TEAL },
  hCard_watch: { borderLeftColor: '#f59e0b' },
  hCard_alert: { borderLeftColor: '#ef4444' },
  hStatus: { fontSize: 10, fontWeight: 600, letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 4 },
  hStatus_good:  { color: TEAL },
  hStatus_watch: { color: '#b45309' },
  hStatus_alert: { color: '#dc2626' },
  hHeadline: { fontSize: 14, fontWeight: 600, color: '#111827', lineHeight: 1.35, marginBottom: 4 },
  hDetail:   { fontSize: 12, color: '#6b7280', lineHeight: 1.5, marginBottom: 10 },
  hBtn: {
    fontSize: 11, color: TEAL, background: '#e6f4f5',
    border: `1px solid #b3dfe1`, borderRadius: 20, padding: '3px 10px',
    cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
  },

  /* Chat thread */
  thread: {
    width: '100%', maxWidth: 760,
    display: 'flex', flexDirection: 'column',
    marginBottom: 16,
  },

  /* Message group (one Q + one A) */
  messageGroup: {
    display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 28,
  },

  /* User bubble */
  userBubble: { display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-start', gap: 10 },
  userAvatar: {
    fontSize: 11, fontWeight: 600, color: '#6b7280',
    background: '#f3f4f6', border: '1px solid #e5e7eb',
    borderRadius: 20, padding: '2px 9px', whiteSpace: 'nowrap', marginTop: 4,
  },
  userText: {
    background: TEAL, color: '#fff',
    padding: '10px 16px', borderRadius: '16px 16px 4px 16px',
    fontSize: 15, fontWeight: 500, maxWidth: '80%', lineHeight: 1.5,
  },

  /* AI bubble */
  aiBubble: { display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 2 },
  aiAvatar: { fontSize: 18, color: TEAL, marginTop: 8, flexShrink: 0 },
  aiContent: {
    flex: 1, background: '#f9fafb', border: '1px solid #e5e7eb',
    borderRadius: '4px 16px 16px 16px', padding: '16px 20px',
  },

  /* Loading dots */
  loadingDots: { display: 'flex', gap: 5, padding: '4px 0' },
  dot: {
    width: 8, height: 8, borderRadius: '50%', background: TEAL,
    animation: 'pulse 1.4s ease-in-out infinite', display: 'inline-block',
  },

  /* Error */
  errorInline: {
    fontSize: 14, color: '#7f1d1d', lineHeight: 1.5,
    display: 'flex', alignItems: 'flex-start', gap: 6, flexWrap: 'wrap',
  },
  retryBtn: {
    fontSize: 12, color: TEAL, background: '#e6f4f5',
    border: '1px solid #b3dfe1', borderRadius: 6,
    padding: '3px 10px', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
    marginLeft: 4,
  },

  /* Data table */
  tableWrapper: {
    overflowX: 'auto',
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    maxHeight: 380,
    overflowY: 'auto',
  },
  dataTable: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
    background: '#fff',
  },
  dataTh: {
    padding: '8px 14px',
    textAlign: 'left',
    fontSize: 11,
    fontWeight: 600,
    color: '#6b7280',
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
    background: '#f9fafb',
    borderBottom: '1px solid #e5e7eb',
    whiteSpace: 'nowrap',
  },
  dataTd: {
    padding: '8px 14px',
    color: '#374151',
    borderBottom: '1px solid #f3f4f6',
    whiteSpace: 'nowrap',
  },

  /* Answer content */
  answerText: { fontSize: 16, color: '#111827', lineHeight: 1.7, fontWeight: 400, margin: 0 },
  sources: {
    marginTop: 12, fontSize: 12, color: '#6b7280',
    background: '#fff', border: '1px solid #e5e7eb',
    borderRadius: 6, padding: '7px 12px', lineHeight: 1.5,
  },
  sourcesLabel: { fontWeight: 600, color: TEAL, marginRight: 4 },
  metaRow: { marginTop: 12, display: 'flex', alignItems: 'center', gap: 10 },
  rowPill: {
    fontSize: 12, color: TEAL, background: '#e6f4f5',
    border: '1px solid #b3dfe1', borderRadius: 20, padding: '3px 10px', fontWeight: 500,
  },

  /* Chart */
  chartWrapper: { marginTop: 16, borderTop: '1px solid #f3f4f6', paddingTop: 14 },
  downloadBtn: {
    marginTop: 8, fontSize: 12, color: '#6b7280',
    background: 'none', border: '1px solid #e5e7eb', borderRadius: 6,
    padding: '4px 12px', cursor: 'pointer', fontFamily: 'inherit',
  },

  /* SQL */
  sqlWrapper: { marginTop: 16, borderTop: '1px solid #f3f4f6', paddingTop: 14 },
  sqlToggle: {
    display: 'flex', alignItems: 'center', gap: 6,
    fontSize: 13, color: '#6b7280', background: 'none', border: 'none',
    cursor: 'pointer', fontFamily: 'inherit', padding: 0, fontWeight: 500,
  },
  sqlToggleIcon: { color: TEAL, fontSize: 14 },
  sqlBlock: {
    marginTop: 10, padding: '14px 18px', background: '#f8fafc',
    border: '1px solid #e5e7eb', borderRadius: 8,
    fontSize: 13, color: '#374151',
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    overflowX: 'auto', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  },

  /* Input section — sticky */
  inputSection: {
    width: '100%', maxWidth: 760,
    position: 'sticky', bottom: 0,
    background: '#fff',
    paddingTop: 12, paddingBottom: 4,
    borderTop: '1px solid #f3f4f6',
  },
  chips: { display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 },
  chip: {
    padding: '7px 14px', background: '#f9fafb', border: '1px solid #e5e7eb',
    borderRadius: 20, fontSize: 13, color: '#374151', cursor: 'pointer',
    fontFamily: 'inherit', transition: 'all 0.15s', lineHeight: 1.4,
  },
  form: { width: '100%' },
  inputRow: { display: 'flex', gap: 10, width: '100%' },
  input: {
    flex: 1, padding: '13px 18px', fontSize: 16,
    border: '2px solid #e5e7eb', borderRadius: 10, outline: 'none',
    color: '#111827', background: '#fff', transition: 'border-color 0.15s',
    fontFamily: 'inherit',
  },
  button: {
    padding: '13px 28px', background: TEAL, color: '#fff', border: 'none',
    borderRadius: 10, fontSize: 16, fontWeight: 600, cursor: 'pointer',
    transition: 'opacity 0.15s', fontFamily: 'inherit',
    display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: 76,
  },
  buttonDisabled: { opacity: 0.45, cursor: 'not-allowed' },
  spinner: {
    width: 18, height: 18, border: '2.5px solid rgba(255,255,255,0.35)',
    borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block',
    animation: 'spin 0.7s linear infinite',
  },

  /* Footer */
  footer: {
    marginTop: 'auto', paddingTop: 32,
    width: '100%', maxWidth: 760,
    borderTop: '1px solid #f3f4f6', textAlign: 'center',
  },
  footerText: { fontSize: 13, color: '#9ca3af' },
  footerLink: { color: TEAL, textDecoration: 'none' },
};
