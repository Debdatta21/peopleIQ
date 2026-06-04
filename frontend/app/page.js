'use client';

import { useState, useRef, useEffect, useCallback } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const EXAMPLE_QUESTIONS = [
  'What is our current total headcount?',
  'What is our attrition rate this quarter?',
  'Which locations have the highest turnover?',
  'How long does it take us to fill a role on average?',
  'Which departments grew the most this year?',
  'How many people left within their first 90 days?',
];

export default function Home() {
  const [question, setQuestion]       = useState('');
  const [answer, setAnswer]           = useState(null);
  const [sql, setSql]                 = useState(null);
  const [rowCount, setRowCount]       = useState(null);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState(null);
  const [sqlExpanded, setSqlExpanded] = useState(false);
  const [summary, setSummary]         = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [chartData, setChartData]     = useState(null);
  const inputRef                      = useRef(null);
  const chartRef                      = useRef(null);

  // Load workforce brief on mount
  useEffect(() => {
    fetch(`${API_URL}/summary`)
      .then(r => r.json())
      .then(d => { setSummary(d.metrics); setSummaryLoading(false); })
      .catch(() => setSummaryLoading(false));
  }, []);

  // Draw chart — runs after chartData AND after canvas is guaranteed in DOM
  useEffect(() => {
    if (!chartData || !chartRef.current) return;
    // Small defer to ensure canvas is painted before we draw
    const id = setTimeout(() => {
    if (!chartRef.current) return;
    const canvas = chartRef.current;
    const ctx    = canvas.getContext('2d');
    const { labels, values, label, type } = chartData;
    const W = canvas.width, H = canvas.height;
    const PAD = { top: 20, right: 16, bottom: 48, left: 56 };
    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;
    const maxVal = Math.max(...values) * 1.1 || 1;
    const TEAL   = '#0D7377';
    const TEAL_L = '#e6f4f5';
    const GRAY   = '#6b7280';
    const LGRAY  = '#e5e7eb';

    ctx.clearRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = LGRAY;
    ctx.lineWidth   = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = PAD.top + chartH - (i / 4) * chartH;
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + chartW, y); ctx.stroke();
      ctx.fillStyle = GRAY; ctx.font = '11px system-ui';
      ctx.textAlign = 'right';
      ctx.fillText(((maxVal / 1.1) * i / 4).toFixed(maxVal > 100 ? 0 : 1), PAD.left - 6, y + 3);
    }

    if (type === 'bar') {
      const barW  = Math.min(chartW / labels.length * 0.6, 48);
      const step  = chartW / labels.length;
      values.forEach((v, i) => {
        const x  = PAD.left + i * step + step / 2 - barW / 2;
        const bH = (v / maxVal) * chartH;
        const y  = PAD.top + chartH - bH;
        ctx.fillStyle = TEAL_L;
        ctx.fillRect(x, PAD.top, barW, chartH);
        ctx.fillStyle = TEAL;
        ctx.fillRect(x, y, barW, bH);
        ctx.fillStyle = '#111827'; ctx.font = '11px system-ui'; ctx.textAlign = 'center';
        const lbl = String(labels[i]).length > 10 ? String(labels[i]).slice(0,10)+'…' : String(labels[i]);
        ctx.fillText(lbl, x + barW / 2, PAD.top + chartH + 14);
      });
    } else {
      // Line chart
      ctx.strokeStyle = TEAL; ctx.lineWidth = 2; ctx.lineJoin = 'round';
      ctx.beginPath();
      values.forEach((v, i) => {
        const x = PAD.left + (i / (values.length - 1)) * chartW;
        const y = PAD.top + chartH - (v / maxVal) * chartH;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
      values.forEach((v, i) => {
        const x = PAD.left + (i / (values.length - 1)) * chartW;
        const y = PAD.top + chartH - (v / maxVal) * chartH;
        ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = TEAL; ctx.fill();
        ctx.fillStyle = '#111827'; ctx.font = '11px system-ui'; ctx.textAlign = 'center';
        const lbl = String(labels[i]).length > 8 ? String(labels[i]).slice(0,8)+'…' : String(labels[i]);
        ctx.fillText(lbl, x, PAD.top + chartH + 14);
      });
    }

    // Chart label
    ctx.fillStyle = GRAY; ctx.font = '500 12px system-ui'; ctx.textAlign = 'left';
    ctx.fillText(label, PAD.left, 13);
    }, 0);
    return () => clearTimeout(id);
  }, [chartData]);

  const handleChip = (q) => {
    setQuestion(q);
    inputRef.current?.focus();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const q = question.trim();
    if (!q) return;

    setLoading(true);
    setAnswer(null);
    setSql(null);
    setRowCount(null);
    setError(null);
    setSqlExpanded(false);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        if (res.status === 429) {
          throw new Error('⏳ Groq is rate-limited right now. Wait 30–60 seconds and try again.');
        }
        throw new Error(detail?.detail || `Server error ${res.status}`);
      }

      const data = await res.json();
      setAnswer(data.answer);
      setSql(data.sql);
      setRowCount(data.row_count);
      setChartData(data.chart_data || null);
    } catch (err) {
      setError(err.message || 'Something went wrong. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={styles.main}>
      {/* ── Header ── */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>◈</span>
          <span style={styles.logoText}>PeopleIQ</span>
        </div>
        <span style={styles.badge}>Phase 2 Demo</span>
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

      {/* ── Hero ── */}
      <section style={styles.hero}>
        <h1 style={styles.h1}>Ask anything about your workforce.</h1>
        <p style={styles.subheadline}>
          Natural language people analytics — powered by 500 synthetic employees across 7 years of generated HR data.
        </p>
      </section>

      {/* ── Workforce Brief ── */}
      <section style={styles.briefSection}>
        <div style={styles.briefHeader}>
          <span style={styles.briefLabel}>Workforce brief</span>
          <span style={styles.briefDate}>
            {new Date().toLocaleDateString('en-US', { weekday:'long', year:'numeric', month:'long', day:'numeric' })}
          </span>
        </div>

        {summaryLoading ? (
          <div style={styles.briefSkeleton}>
            {[...Array(6)].map((_, i) => (
              <div key={i} style={styles.skeletonCard} />
            ))}
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

      {/* ── Search form ── */}
      <section style={styles.searchSection}>
        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.inputRow}>
            <input
              ref={inputRef}
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. What is our attrition rate this quarter?"
              style={styles.input}
              disabled={loading}
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="submit"
              disabled={loading || !question.trim()}
              style={{
                ...styles.button,
                ...(loading || !question.trim() ? styles.buttonDisabled : {}),
              }}
            >
              {loading ? (
                <span style={styles.spinner} />
              ) : (
                'Ask'
              )}
            </button>
          </div>
        </form>

        {/* ── Example question chips ── */}
        <div style={styles.chips}>
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => handleChip(q)}
              style={styles.chip}
              type="button"
            >
              {q}
            </button>
          ))}
        </div>
      </section>

      {/* ── Answer area ── */}
      {(answer || error || loading) && (
        <section style={styles.answerSection}>
          {loading && (
            <div style={styles.loadingBox}>
              <div style={styles.loadingDots}>
                <span style={{ ...styles.dot, animationDelay: '0ms' }} />
                <span style={{ ...styles.dot, animationDelay: '160ms' }} />
                <span style={{ ...styles.dot, animationDelay: '320ms' }} />
              </div>
              <p style={styles.loadingText}>Generating answer…</p>
            </div>
          )}

          {error && !loading && (
            <div style={styles.errorBox}>
              <span style={styles.errorIcon}>⚠</span>
              <p style={styles.errorText}>{error}</p>
            </div>
          )}

          {answer && !loading && (
            <div style={styles.answerBox}>
              {/* Question echo */}
              <p style={styles.questionEcho}>"{question}"</p>

              {/* Answer */}
              {(() => {
                const parts = answer.split(/\nData sources:/i);
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

              {/* Chart — always rendered so ref is always attached; hidden when no data */}
              <div style={{ ...styles.chartWrapper, display: chartData ? 'block' : 'none' }}>
                <canvas
                  ref={chartRef}
                  width={700}
                  height={220}
                  style={styles.chartCanvas}
                />
              </div>

              {/* Row count pill */}
              {rowCount !== null && (
                <div style={styles.metaRow}>
                  <span style={styles.rowPill}>
                    {rowCount} {rowCount === 1 ? 'row' : 'rows'} returned
                  </span>
                </div>
              )}

              {/* Collapsible SQL */}
              {sql && (
                <div style={styles.sqlWrapper}>
                  <button
                    onClick={() => setSqlExpanded((v) => !v)}
                    style={styles.sqlToggle}
                    type="button"
                  >
                    <span style={styles.sqlToggleIcon}>
                      {sqlExpanded ? '▾' : '▸'}
                    </span>
                    How this was calculated
                  </button>
                  {sqlExpanded && (
                    <pre style={styles.sqlBlock}>
                      <code>{sql}</code>
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

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

  /* Disclaimer */
  disclaimer: {
    width: '100%',
    maxWidth: 760,
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 16px',
    background: '#fffbeb',
    border: '1px solid #fcd34d',
    borderRadius: 8,
    fontSize: 13,
    color: '#92400e',
    lineHeight: 1.5,
    marginBottom: 28,
  },
  disclaimerIcon: {
    flexShrink: 0,
    marginTop: 1,
    fontSize: 14,
  },

  /* Header */
  header: {
    width: '100%',
    maxWidth: 760,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '20px 0 0',
    marginBottom: 40,
    borderBottom: '1px solid #e5e7eb',
    paddingBottom: 16,
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  logoIcon: {
    fontSize: 22,
    color: TEAL,
  },
  logoText: {
    fontWeight: 700,
    fontSize: 18,
    color: '#111827',
    letterSpacing: '-0.3px',
  },
  badge: {
    fontSize: 12,
    fontWeight: 500,
    color: TEAL,
    background: '#e6f4f5',
    border: '1px solid #b3dfe1',
    borderRadius: 20,
    padding: '3px 10px',
  },

  /* Hero */
  hero: {
    textAlign: 'center',
    maxWidth: 620,
    marginBottom: 36,
  },
  h1: {
    fontSize: 'clamp(26px, 5vw, 40px)',
    fontWeight: 800,
    color: '#111827',
    letterSpacing: '-0.8px',
    lineHeight: 1.2,
    marginBottom: 10,
  },
  subheadline: {
    fontSize: 17,
    color: '#6b7280',
    fontWeight: 400,
  },

  /* Search */
  searchSection: {
    width: '100%',
    maxWidth: 760,
    marginBottom: 12,
  },
  form: {
    width: '100%',
    marginBottom: 14,
  },
  inputRow: {
    display: 'flex',
    gap: 10,
    width: '100%',
  },
  input: {
    flex: 1,
    padding: '14px 18px',
    fontSize: 16,
    border: '2px solid #e5e7eb',
    borderRadius: 10,
    outline: 'none',
    color: '#111827',
    background: '#fff',
    transition: 'border-color 0.15s',
    fontFamily: 'inherit',
  },
  button: {
    padding: '14px 28px',
    background: TEAL,
    color: '#fff',
    border: 'none',
    borderRadius: 10,
    fontSize: 16,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
    fontFamily: 'inherit',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 76,
  },
  buttonDisabled: {
    opacity: 0.45,
    cursor: 'not-allowed',
  },
  spinner: {
    width: 18,
    height: 18,
    border: '2.5px solid rgba(255,255,255,0.35)',
    borderTopColor: '#fff',
    borderRadius: '50%',
    display: 'inline-block',
    animation: 'spin 0.7s linear infinite',
  },

  /* Chips */
  chips: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    padding: '7px 14px',
    background: '#f9fafb',
    border: '1px solid #e5e7eb',
    borderRadius: 20,
    fontSize: 13,
    color: '#374151',
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
    lineHeight: 1.4,
  },

  /* Answer area */
  answerSection: {
    width: '100%',
    maxWidth: 760,
    marginTop: 28,
  },

  /* Loading */
  loadingBox: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '32px 24px',
    background: '#f9fafb',
    borderRadius: 12,
    border: '1px solid #e5e7eb',
  },
  loadingDots: {
    display: 'flex',
    gap: 6,
    marginBottom: 12,
  },
  dot: {
    width: 9,
    height: 9,
    borderRadius: '50%',
    background: TEAL,
    animation: 'pulse 1.4s ease-in-out infinite',
    display: 'inline-block',
  },
  loadingText: {
    fontSize: 14,
    color: '#6b7280',
  },

  /* Error */
  errorBox: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '16px 20px',
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 10,
  },
  errorIcon: {
    color: '#dc2626',
    fontSize: 16,
    marginTop: 2,
    flexShrink: 0,
  },
  errorText: {
    fontSize: 14,
    color: '#7f1d1d',
    lineHeight: 1.5,
  },

  /* Answer card */
  answerBox: {
    background: '#ffffff',
    border: '1px solid #e5e7eb',
    borderRadius: 12,
    padding: '24px 28px',
    boxShadow: '0 2px 12px rgba(0,0,0,.07)',
  },
  questionEcho: {
    fontSize: 13,
    color: '#9ca3af',
    marginBottom: 14,
    fontStyle: 'italic',
  },
  answerText: {
    fontSize: 17,
    color: '#111827',
    lineHeight: 1.7,
    fontWeight: 400,
  },
  metaRow: {
    marginTop: 16,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  rowPill: {
    fontSize: 12,
    color: TEAL,
    background: '#e6f4f5',
    border: '1px solid #b3dfe1',
    borderRadius: 20,
    padding: '3px 10px',
    fontWeight: 500,
  },

  /* Data sources */
  sources: {
    marginTop: 14,
    fontSize: 12,
    color: '#6b7280',
    background: '#f9fafb',
    border: '1px solid #e5e7eb',
    borderRadius: 6,
    padding: '7px 12px',
    lineHeight: 1.5,
  },
  sourcesLabel: {
    fontWeight: 600,
    color: TEAL,
    marginRight: 4,
  },

  /* SQL collapsible */
  sqlWrapper: {
    marginTop: 20,
    borderTop: '1px solid #f3f4f6',
    paddingTop: 16,
  },
  sqlToggle: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 13,
    color: '#6b7280',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    fontFamily: 'inherit',
    padding: 0,
    fontWeight: 500,
  },
  sqlToggleIcon: {
    color: TEAL,
    fontSize: 14,
  },
  sqlBlock: {
    marginTop: 12,
    padding: '14px 18px',
    background: '#f8fafc',
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    fontSize: 13,
    color: '#374151',
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    overflowX: 'auto',
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },

  /* ── Workforce Brief ── */
  briefSection: {
    width: '100%',
    maxWidth: 760,
    marginBottom: 28,
  },
  briefHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  briefLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: '#6b7280',
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
  },
  briefDate: {
    fontSize: 11,
    color: '#9ca3af',
  },
  briefGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 10,
  },
  briefSkeleton: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 10,
  },
  skeletonCard: {
    height: 96,
    background: '#f3f4f6',
    borderRadius: 10,
    animation: 'pulse 1.4s ease-in-out infinite',
  },
  hCard: {
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: 10,
    padding: '14px 16px 12px',
    borderLeft: '3px solid #e5e7eb',
  },
  hCard_good: {
    borderLeftColor: TEAL,
  },
  hCard_watch: {
    borderLeftColor: '#f59e0b',
  },
  hCard_alert: {
    borderLeftColor: '#ef4444',
  },
  hStatus: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  hStatus_good: { color: TEAL },
  hStatus_watch: { color: '#b45309' },
  hStatus_alert: { color: '#dc2626' },
  hHeadline: {
    fontSize: 14,
    fontWeight: 600,
    color: '#111827',
    lineHeight: 1.35,
    marginBottom: 4,
  },
  hDetail: {
    fontSize: 12,
    color: '#6b7280',
    lineHeight: 1.5,
    marginBottom: 10,
  },
  hBtn: {
    fontSize: 11,
    color: TEAL,
    background: '#e6f4f5',
    border: `1px solid #b3dfe1`,
    borderRadius: 20,
    padding: '3px 10px',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontWeight: 500,
  },

  /* ── Chart ── */
  chartWrapper: {
    marginTop: 20,
    borderTop: '1px solid #f3f4f6',
    paddingTop: 16,
    overflowX: 'auto',
  },
  chartCanvas: {
    width: '100%',
    maxWidth: 700,
    height: 220,
    display: 'block',
  },

  /* Footer */
  footer: {
    marginTop: 'auto',
    paddingTop: 48,
    width: '100%',
    maxWidth: 760,
    borderTop: '1px solid #f3f4f6',
    textAlign: 'center',
  },
  footerText: {
    fontSize: 13,
    color: '#9ca3af',
  },
  footerLink: {
    color: TEAL,
    textDecoration: 'none',
  },
};
