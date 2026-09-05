import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts'

const API_BASE = '/api'

function formatINR(paise) {
  if (!paise && paise !== 0) return '₹0'
  return '₹' + (paise / 100).toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

const REASON_COLORS = {
  insufficient_funds: '#f5a623',
  bank_server_downtime: '#3aa7f0',
  otp_timeout: '#8b7cf6',
  wrong_cvv_pin: '#ff5c5c',
  network_drop: '#2fd573',
  expired_card: '#e84393',
  risk_fraud_block: '#b2bec3',
  ambiguous: '#e17055',
  unknown: '#636e72',
}

const TIMING_LABELS = {
  immediate: 'Immediate',
  short_delay: 'Short delay',
  scheduled: 'Scheduled',
  no_retry: 'No retry',
}

const SOURCE_LABELS = {
  rule: 'Rule',
  heuristic: 'Heuristic',
  llm: 'LLM',
  human: 'Human',
  simulation: 'Sim',
}

function App() {
  const [stats, setStats] = useState(null)
  const [records, setRecords] = useState([])
  const [review, setReview] = useState([])
  const [simTime, setSimTime] = useState('')
  const [simulating, setSimulating] = useState(false)
  const [advancing, setAdvancing] = useState(false)
  const [counterDelta, setCounterDelta] = useState(0)
  const [selectedTxn, setSelectedTxn] = useState(null)
  const [audit, setAudit] = useState([])
  const [merchantCfg, setMerchantCfg] = useState(null)
  const bootRef = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const [d, rec, rev, health] = await Promise.all([
        fetch(`${API_BASE}/dashboard`).then((r) => r.json()),
        fetch(`${API_BASE}/records?limit=50`).then((r) => r.json()),
        fetch(`${API_BASE}/review`).then((r) => r.json()),
        fetch(`${API_BASE}/health`).then((r) => r.json()),
      ])
      setStats(d)
      setRecords(rec)
      setReview(rev)
      setSimTime(health.sim_time)
    } catch (e) {
      console.error(e)
    }
  }, [])

  useEffect(() => {
    if (bootRef.current) {
      bootRef.current = false
      refresh()
    }
  }, [refresh])

  const simulate = useCallback(async (count = 25) => {
    if (simulating) return
    setSimulating(true)
    try {
      setCounterDelta(0)
      const res = await fetch(`${API_BASE}/events/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count }),
      })
      const data = await res.json()
      setCounterDelta(data.recovered_value)
      await refresh()
      setTimeout(() => setCounterDelta(0), 3000)
    } catch (e) {
      console.error(e)
    } finally {
      setSimulating(false)
    }
  }, [simulating, refresh])

  const advance = useCallback(async (hours) => {
    if (advancing) return
    setAdvancing(true)
    try {
      await fetch(`${API_BASE}/sim/advance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours }),
      })
      setCounterDelta(0)
      await refresh()
    } catch (e) {
      console.error(e)
    } finally {
      setAdvancing(false)
    }
  }, [advancing, refresh])

  const reset = useCallback(async () => {
    await fetch(`${API_BASE}/sim/reset`, { method: 'POST' })
    setCounterDelta(0)
    setRecords([])
    setReview([])
    await refresh()
  }, [refresh])

  const reviewAction = useCallback(async (id, action) => {
    await fetch(`${API_BASE}/review/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: id }),
    })
    await refresh()
  }, [refresh])

  const openAudit = useCallback(async (txn) => {
    setSelectedTxn(txn)
    setAudit([])
    try {
      const a = await fetch(`${API_BASE}/audit/${txn}`).then((r) => r.json())
      setAudit(a)
    } catch (e) {
      console.error(e)
    }
  }, [])

  const openMerchant = useCallback(async (merchantId) => {
    try {
      const cfg = await fetch(`${API_BASE}/merchants/${merchantId}`).then((r) => r.json())
      setMerchantCfg(cfg)
    } catch (e) {
      console.error(e)
    }
  }, [])

  const saveMerchant = useCallback(async () => {
    if (!merchantCfg) return
    await fetch(`${API_BASE}/merchants/${merchantCfg.merchant_id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: merchantCfg }),
    })
    setMerchantCfg(null)
    await refresh()
  }, [merchantCfg, refresh])

  return (
    <div className="app">
      <Header simulate={simulate} simulating={simulating} reset={reset} />
      <SimClockBar simTime={simTime} advance={advance} advancing={advancing} />
      {stats ? (
        <>
          <StatsRow stats={stats} counterDelta={counterDelta} />
          <div className="dashboard-grid">
            <div className="grid-left">
              <ComparisonCard stats={stats} />
              <PipelineCard stats={stats} onOpenMerchant={openMerchant} />
              <ReasonBreakdownCard stats={stats} />
              <TimelineCard stats={stats} />
            </div>
            <div className="grid-right">
              <ReviewQueue review={review} onAction={reviewAction} />
              <LiveFeed records={records} onOpenAudit={openAudit} onSimulate={simulate} simulating={simulating} />
            </div>
          </div>
        </>
      ) : (
        <div className="loading"><div className="spinner" /></div>
      )}

      {selectedTxn && (
        <div className="modal-backdrop" onClick={() => setSelectedTxn(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="card-title">Decision Audit Trail</div>
                <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{selectedTxn}</div>
              </div>
              <button className="close-btn" onClick={() => setSelectedTxn(null)}>×</button>
            </div>
            <div className="audit-timeline">
              {audit.length === 0 && <div className="empty">Loading audit trail…</div>}
              {audit.map((a) => (
                <div className="audit-item" key={a.id}>
                  <div className="audit-head">
                    <span className="chip source">{a.source}</span>
                    <span className="audit-stage">{a.stage}</span>
                    <span className="audit-time">{String(a.sim_time).replace('T', ' ').slice(0, 19)}</span>
                  </div>
                  <pre className="audit-detail">{JSON.stringify(JSON.parse(a.detail), null, 1)}</pre>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {merchantCfg && (
        <div className="modal-backdrop" onClick={() => setMerchantCfg(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div className="card-title">Merchant Config — {merchantCfg.merchant_id}</div>
              <button className="close-btn" onClick={() => setMerchantCfg(null)}>×</button>
            </div>
            <div className="cfg-form">
              <label>
                Retry sensitivity
                <select
                  value={merchantCfg.sensitivity}
                  onChange={(e) => setMerchantCfg({ ...merchantCfg, sensitivity: e.target.value })}
                >
                  <option value="aggressive">Aggressive</option>
                  <option value="balanced">Balanced</option>
                  <option value="conservative">Conservative</option>
                </select>
              </label>
              <label>
                Max attempts override (blank = policy default)
                <input
                  type="number"
                  min="0"
                  max="4"
                  placeholder="e.g. 2"
                  value={merchantCfg.max_attempts_override ?? ''}
                  onChange={(e) =>
                    setMerchantCfg({
                      ...merchantCfg,
                      max_attempts_override: e.target.value === '' ? null : parseInt(e.target.value, 10),
                    })
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={!!merchantCfg.auto_retry_risk}
                  onChange={(e) => setMerchantCfg({ ...merchantCfg, auto_retry_risk: e.target.checked })}
                />
                Allow auto-retry on risk/fraud blocks (dangerous, off by default)
              </label>
              <label>
                Message channel
                <select
                  value={merchantCfg.message_channel}
                  onChange={(e) => setMerchantCfg({ ...merchantCfg, message_channel: e.target.value })}
                >
                  <option value="whatsapp">WhatsApp</option>
                  <option value="email">Email</option>
                  <option value="sms">SMS</option>
                </select>
              </label>
              <div className="cfg-actions">
                <button className="simulate-btn" onClick={saveMerchant}>Save</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Header({ simulate, simulating, reset }) {
  return (
    <div className="header">
      <div className="brand">
        <div className="brand-logo">R</div>
        <div>
          <h1>Revive</h1>
          <div className="tagline">AI-Powered Payment Recovery Engine</div>
        </div>
      </div>
      <div className="header-actions">
        <div className="live-badge">Live simulation</div>
        <button className="simulate-btn ghost" onClick={reset}>Reset</button>
        <button className="simulate-btn" disabled={simulating} onClick={() => simulate(25)}>
          {simulating ? 'Processing failures…' : '⚡ Stream failure events'}
        </button>
      </div>
    </div>
  )
}

function SimClockBar({ simTime, advance, advancing }) {
  const pretty = simTime ? String(simTime).replace('T', ' ').replace('Z', '').slice(0, 19) + ' UTC' : '—'
  return (
    <div className="sim-bar">
      <div className="sim-label">
        <span className="sim-dot" />
        Sim clock
        <span className="sim-time">{pretty}</span>
      </div>
      <div className="sim-actions">
        <button className="simulate-btn ghost" disabled={advancing} onClick={() => advance(2)}>＋2h</button>
        <button className="simulate-btn ghost" disabled={advancing} onClick={() => advance(12)}>＋12h</button>
        <button className="simulate-btn ghost" disabled={advancing} onClick={() => advance(24)}>＋1 day</button>
      </div>
    </div>
  )
}

function StatsRow({ stats, counterDelta }) {
  if (!stats) return null
  const c = stats.comparison || {}
  return (
    <div className="stats-grid">
      <div className="stat-card">
        <div className="stat-label">Failed Transaction Value</div>
        <div className="stat-value red">{formatINR(stats.total_failed_value)}</div>
        <div className="stat-sub">{stats.total_failed_count} transactions</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Recovered Value</div>
        <div className="stat-value green">{formatINR(stats.total_recovered_value)}</div>
        {counterDelta > 0 && <div className="stat-sub" style={{ color: 'var(--green)' }}>+{formatINR(counterDelta)} just now</div>}
        {counterDelta === 0 && <div className="stat-sub">{stats.total_recovered_count} transactions recovered</div>}
      </div>
      <div className="stat-card">
        <div className="stat-label">Recovery Rate</div>
        <div className="stat-value primary">{stats.recovery_rate.toFixed(1)}%</div>
        <div className="stat-sub">of failed value reviveed</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">vs. Blind Retry</div>
        <div className="stat-value">
          {c.baseline_rate > 0 ? `+${(c.revive_rate - c.baseline_rate).toFixed(1)}pp` : '—'}
        </div>
        <div className="stat-sub">uplift in recovery rate</div>
      </div>
    </div>
  )
}

function ComparisonCard({ stats }) {
  if (!stats || !stats.comparison) return null
  const c = stats.comparison
  const max = Math.max(c.revive_rate, c.baseline_rate, 1)
  return (
    <div className="card">
      <div className="card-title">Revive vs. Blind Retry Baseline</div>
      <div className="comparison-chart">
        <div className="compare-row">
          <div className="compare-label">Revive</div>
          <div className="compare-bar-wrap">
            <div className="compare-bar revive" style={{ width: `${(c.revive_rate / max) * 100}%` }}>
              {formatINR(c.revive_value)}
            </div>
          </div>
          <div className="compare-pct" style={{ color: 'var(--green)' }}>{c.revive_rate.toFixed(1)}%</div>
        </div>
        <div className="compare-row">
          <div className="compare-label">Blind Retry</div>
          <div className="compare-bar-wrap">
            <div className="compare-bar baseline" style={{ width: `${(c.baseline_rate / max) * 100}%` }}>
              {formatINR(c.baseline_value)}
            </div>
          </div>
          <div className="compare-pct" style={{ color: 'var(--text-dim)' }}>{c.baseline_rate.toFixed(1)}%</div>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
          Same failed pool, same volume. Reason-aware timing recovers {formatINR(Math.max(c.revive_value - c.baseline_value, 0))} more.
        </div>
      </div>
    </div>
  )
}

function PipelineCard({ stats, onOpenMerchant }) {
  if (!stats || !stats.retry_pipeline) return null
  const p = stats.retry_pipeline
  return (
    <div className="card">
      <div className="card-title">Retry Pipeline</div>
      <div className="pipeline-row">
        <div className="pipeline-cell">
          <div className="pipeline-num">{p.scheduled_attempts}</div>
          <div className="pipeline-label">Scheduled retries</div>
        </div>
        <div className="pipeline-cell">
          <div className="pipeline-num">{p.executed_attempts}</div>
          <div className="pipeline-label">Executed attempts</div>
        </div>
        <div className="pipeline-cell">
          <div className="pipeline-num amber">{p.pending_review}</div>
          <div className="pipeline-label">In human review</div>
        </div>
      </div>
      <div className="pipeline-src">
        <span className="chip source rule">Rule {stats.by_source?.rule || 0}</span>
        <span className="chip source heuristic">Heuristic {stats.by_source?.heuristic || 0}</span>
        <span className="chip source llm">LLM {stats.by_source?.llm || 0}</span>
        <span className="chip source human">Human {stats.by_source?.human || 0}</span>
      </div>
    </div>
  )
}

function ReviewQueue({ review, onAction }) {
  return (
    <div className="card">
      <div className="card-title">Human Review Queue</div>
      {review.length === 0 ? (
        <div className="empty">Queue is clear — no escalated payments awaiting approval.</div>
      ) : (
        <div className="review-list">
          {review.map((item) => (
            <div className="review-item" key={item.id}>
              <div className="feed-row">
                <span className="feed-txn">{item.transaction_id.slice(-8)}</span>
                <span className="feed-amount">{formatINR(item.amount)}</span>
              </div>
              <div className="feed-meta">
                <span className="chip reason">{item.category.replace('_', ' ')}</span>
                <span className="chip bank">{item.merchant_name || item.merchant_id}</span>
              </div>
              <div className="review-reason">{item.reason}</div>
              <div className="review-actions">
                <button className="mini-btn approve" onClick={() => onAction(item.id, 'approve')}>Approve one retry</button>
                <button className="mini-btn dismiss" onClick={() => onAction(item.id, 'dismiss')}>Dismiss</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ReasonBreakdownCard({ stats }) {
  if (!stats) return null
  const reasons = Object.entries(stats.by_reason).sort((a, b) => b[1].value_failed - a[1].value_failed)
  return (
    <div className="card">
      <div className="card-title">Failure Reasons & Recovery</div>
      <table className="reason-table">
        <thead>
          <tr>
            <th>Reason</th>
            <th>Failed</th>
            <th>Recovered</th>
            <th>Recovery %</th>
          </tr>
        </thead>
        <tbody>
          {reasons.map(([key, v]) => (
            <tr key={key}>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: REASON_COLORS[key] || '#888' }} />
                  {v.label}
                </div>
              </td>
              <td>{formatINR(v.value_failed)}</td>
              <td style={{ color: v.value_recovered > 0 ? 'var(--green)' : 'var(--text-dim)' }}>{formatINR(v.value_recovered)}</td>
              <td>
                {v.value_failed > 0 ? ((v.value_recovered / v.value_failed) * 100).toFixed(0) : 0}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TimelineCard({ stats }) {
  if (!stats || !stats.recovery_timeline || stats.recovery_timeline.length === 0) {
    return (
      <div className="card">
        <div className="card-title">Recovered Revenue Timeline</div>
        <div className="empty">No recovered events yet — stream failures, then fast-forward the sim clock.</div>
      </div>
    )
  }
  return (
    <div className="card">
      <div className="card-title">Recovered Revenue Timeline</div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={stats.recovery_timeline}>
          <defs>
            <linearGradient id="recGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3395ff" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#3395ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#232c42" strokeDasharray="3 3" />
          <XAxis dataKey="index" stroke="#8b96ad" fontSize={11} />
          <YAxis stroke="#8b96ad" fontSize={11} tickFormatter={(v) => `₹${(v / 100000).toFixed(0)}L`} />
          <Tooltip
            contentStyle={{ background: '#131a2b', border: '1px solid #232c42', borderRadius: 8, fontSize: 12 }}
            formatter={(value) => [formatINR(value), 'Cumulative recovered']}
          />
          <Area type="monotone" dataKey="cumulative_recovered" stroke="#3395ff" fill="url(#recGrad)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function LiveFeed({ records, onOpenAudit, onSimulate, simulating }) {
  return (
    <div className="card">
      <div className="card-title">Live Recovery Feed <span style={{ fontSize: 11, color: 'var(--text-dim)', fontWeight: 400 }}>(click a row for its audit trail)</span></div>
      {!simulating && records.length === 0 ? (
        <div className="empty">
          No events yet.
          <br /><br />
          <button className="simulate-btn" onClick={() => onSimulate(25)}>⚡ Stream failure events</button>
        </div>
      ) : (
        <div className="feed">
          {records.map((r) => (
            <FeedItem key={r.transaction_id} record={r} onClick={() => onOpenAudit(r.transaction_id)} />
          ))}
          {simulating && <div style={{ color: 'var(--text-dim)', fontSize: 12, textAlign: 'center', padding: 12 }}>Processing…</div>}
        </div>
      )}
    </div>
  )
}

function FeedItem({ record, onClick }) {
  return (
    <div className="feed-item clickable" onClick={onClick}>
      <div className="feed-row">
        <span className="feed-txn">#{record.transaction_id.slice(-8)}</span>
        <span className="feed-amount">{formatINR(record.amount)}</span>
      </div>
      <div className="feed-meta">
        <span className="chip reason">{record.reason_label}</span>
        <span className="chip timing">{TIMING_LABELS[record.retry_timing] || record.retry_timing}</span>
        <span className="chip source">{SOURCE_LABELS[record.source] || record.source}</span>
        {record.payment_link_source && (
          <span className={`chip bank ${record.payment_link_source === 'razorpay' ? 'link-live' : ''}`}>
            Payment link · {record.payment_link_source === 'razorpay' ? 'Razorpay' : 'offline'}
          </span>
        )}
        {record.routing === 'review' && <span className="chip timing amber">Awaiting review</span>}
        {record.retry_attempts > 0 && <span className="chip bank">{record.retry_attempts} attempt{record.retry_attempts > 1 ? 's' : ''}</span>}
        <span className={`chip ${record.recovered ? 'recovered' : 'failed'}`}>
          {record.recovered ? 'Recovered' : record.routing === 'review' ? 'In review' : 'Pending retry'}
        </span>
      </div>
      <div className="feed-message">“{record.message}”</div>
    </div>
  )
}

export default App