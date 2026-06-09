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

// ── Chart renderer (pure function, called per-message) ────────────────────────
function drawChart(canvas, chartData) {
  if (!canvas || !chartData) return;
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
  ctx.strokeStyle = LGRAY; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = PAD.top + chartH - (i / 4) * chartH;
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + chartW, y); ctx.stroke();
    ctx.fillStyle = GRAY; ctx.font = '11px system-ui'; ctx.textAlign = 'right';
    ctx.fillText(((maxVal / 1.1) * i / 4).toFixed(maxVal > 100 ? 0 : 1), PAD.left - 6, y + 3);
  }

  if (type === 'bar') {
    const barW = Math.min(chartW / labels.length * 0.6, 48);
    const step = chartW / labels.length;
    values.forEach((v, i) => {
      const x  = PAD.left + i * step + step / 2 - barW / 2;
      const bH = (v / maxVal) * chartH;
      const y  = PAD.top + chartH - bH;
      ctx.fillStyle = TEAL_L; ctx.fillRect(x, PAD.top, barW, chartH);
      ctx.fillStyle = TEAL;   ctx.fillRect(x, y, barW, bH);
      ctx.fillStyle = '#111827'; ctx.font = '11px system-ui'; ctx.textAlign = 'center';
      const lbl = String(labels[i]).length > 10 ? String(labels[i]).slice(0, 10) + '…' : String(labels[i]);
      ctx.fillText(lbl, x + barW / 2, PAD.top + chartH + 14);
    });
  } else {
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
      const lbl = String(labels[i]).length > 8 ? String(labels[i]).slice(0, 8) + '…' : String(labels[i]);
      ctx.fillText(lbl, x, PAD.top + chartH + 14);
    });
  }

  ctx.fillStyle = GRAY; ctx.font = '500 12px system-ui'; ctx.textAlign = 'left';
  ctx.fillText(label, PAD.left, 13);
}

// ── Single message bubble ─────────────────────────────────────────────────────
function MessageBubble({ msg }) {
  const [sqlExpanded, setSqlExpanded] = useState(false);
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!msg.chartData || !canvasRef.current) return;
    const id = setTimeout(() => drawChart(canvasRef.current, msg.chartData), 0);
    return () => clearTimeout(id);
  }, [msg.chartData]);

  const downloadChart = () => {
    if (!canvasRef.current) return;
    const a = document.createElement('a');
    a.href = canvasRef.current.toDataURL('image/png');
    a.download = `peopleiq-chart-${Date.now()}.png`;
    a.click();
  };

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
              {msg.error}
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

              {/* Chart */}
              {msg.chartData && (
                <div style={styles.chartWrapper}>
                  <canvas ref={canvasRef} width={660} height={220} style={styles.chartCanvas} />
                  <button onClick={downloadChart} style={styles.downloadBtn} type="button">
                    ↓ Download chart
                  </button>
                </div>
              )}

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

  // Load workforce brief on mount
  useEffect(() => {
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

  const handleSubmit = async (e) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;

    // Push a pending message immediately so the user sees their question
    const id = Date.now();
    setMessages(prev => [...prev, { id, question: q, status: 'pending' }]);
    setQuestion('');
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const errMsg = res.status === 429
          ? '⏳ Groq is rate-limited right now. Wait 30–60 seconds and try again.'
          : (detail?.detail || `Server error ${res.status}`);
        setMessages(prev => prev.map(m =>
          m.id === id ? { ...m, status: 'error', error: errMsg } : m
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
        } : m
      ));
    } catch (err) {
      const errMsg = err.message || 'Something went wrong. Is the backend running?';
      setMessages(prev => prev.map(m =>
        m.id === id ? { ...m, status: 'error', error: errMsg } : m
      ));
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
        <div style={styles.headerRight}>
          <a href="/admin" style={styles.adminLink}>Admin ↗</a>
          <span style={styles.badge}>Phase 2 Demo</span>
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

      {/* ── Hero + brief: visible only before first question ── */}
      {messages.length === 0 && (
        <>
          <section style={styles.hero}>
            <h1 style={styles.h1}>Ask anything about your workforce.</h1>
            <p style={styles.subheadline}>
              Natural language people analytics — powered by 500 synthetic employees across 7 years of generated HR data.
            </p>
          </section>

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
        </>
      )}

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
  errorInline: { fontSize: 14, color: '#7f1d1d', lineHeight: 1.5 },

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
  chartCanvas: { width: '100%', maxWidth: 660, height: 220, display: 'block' },
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
