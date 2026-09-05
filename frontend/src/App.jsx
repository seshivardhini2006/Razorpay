import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  Line,
  Area,
  AreaChart,
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
  unknown: '#636e72',
}

const TIMING_LABELS = {
  immediate: 'Immediate',
  short_delay: 'Short delay',
  scheduled: 'Scheduled',
  no_retry: 'No retry',
}

function App() {
  const [stats, setStats] = useState(null)
  const [records, setRecords] = useState([])
  const [loading, setLoading] = useState(true)
  const [simulating, setSimulating] = useState(false)
  const [counterDelta, setCounterDelta] = useState(0)
  const counterAmountRef = useRef(0)
  const bootRef = useRef(true)

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/dashboard`)
      const data = await res.json()
      setStats(data)
    } catch (e) {
      console.error(e)
    }
  }, [])

  const fetchRecords = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/records?limit=40`)
      const data = await res.json()
      setRecords(data)
    } catch (e) {
      console.error(e)
    }
  }, [])

  const refresh = useCallback(() => {
    fetchDashboard()
    fetchRecords()
  }, [fetchDashboard, fetchRecords])

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
      const delta = data.recovered_value
      counterAmountRef.current += delta
      setCounterDelta(delta)
      refresh()
      setTimeout(() => setCounterDelta(0), 3000)
    } catch (e) {
      console.error(e)
    } finally {
      setSimulating(false)
    }
  }, [simulating, refresh])

  // initial load
  useEffect(() => {
    if (bootRef.current) {
      bootRef.current = false
      setLoading(false)
    }
  }, [])

  return (
    <div className="app">
      <Header simulating={simulating} onSimulate={simulate} />
      {loading && stats === null ? (
        <div className="loading"><div className="spinner" /></div>
      ) : (
        <>
          <StatsRow stats={stats} counterDelta={counterDelta} />
          <div className="dashboard-grid">
            <div className="grid-left">
              <ComparisonCard stats={stats} />
              <ReasonBreakdownCard stats={stats} />
              <TimelineCard stats={stats} />
            </div>
            <LiveFeed records={records} onSimulate={simulate} simulating={simulating} />
          </div>
        </>
      )}
    </div>
  )
}

function Header({ simulating, onSimulate }) {
  return (
    <div className="header">
      <div className="brand">
        <div className="brand-logo">R</div>
        <div>
          <h1>Reclaim</h1>
          <div className="tagline">AI-Powered Payment Recovery Engine</div>
        </div>
      </div>
      <div className="header-actions">
        <div className="live-badge">Live simulation</div>
        <button className="simulate-btn" disabled={simulating} onClick={() => onSimulate(25)}>
          {simulating ? 'Processing failures…' : '⚡ Stream failure events'}
        </button>
      </div>
    </div>
  )
}

function StatsRow({ stats, counterDelta }) {
  if (!stats) return null
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
        <div className="stat-sub">of failed value reclaimed</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">vs. Blind Retry</div>
        <div className="stat-value">
          {stats.comparison && stats.comparison.baseline_rate > 0
            ? `+${(stats.comparison.reclaim_rate - stats.comparison.baseline_rate).toFixed(1)}pp`
            : '—'}
        </div>
        <div className="stat-sub">uplift in recovery rate</div>
      </div>
    </div>
  )
}

function ComparisonCard({ stats }) {
  if (!stats || !stats.comparison) return null
  const c = stats.comparison
  const max = Math.max(c.reclaim_rate, c.baseline_rate, 1)
  const baselineValue = c.baseline_value + c.baseline_count * 0 // baseline_value already from API
  return (
    <div className="card">
      <div className="card-title">Reclaim vs. Blind Retry Baseline</div>
      <div className="comparison-chart">
        <div className="compare-row">
          <div className="compare-label">Reclaim</div>
          <div className="compare-bar-wrap">
            <div className="compare-bar reclaim" style={{ width: `${(c.reclaim_rate / max) * 100}%` }}>
              {formatINR(c.reclaim_value)}
            </div>
          </div>
          <div className="compare-pct" style={{ color: 'var(--green)' }}>{c.reclaim_rate.toFixed(1)}%</div>
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
          Same failed pool, same transaction volume. Reclaim's reason-aware timing recovers {formatINR(c.reclaim_value - c.baseline_value)} more.
        </div>
      </div>
    </div>
  )
}

function ReasonBreakdownCard({ stats }) {
  if (!stats) return null
  const reasons = Object.entries(stats.by_reason).sort((a, b) => b[1].value_failed - a[1].value_failed)
  const maxValue = Math.max(...reasons.map(([, v]) => v.value_failed), 1)
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
        <div className="empty">No recovered events yet — stream some failure events to see the counter move.</div>
      </div>
    )
  }
  const data = stats.recovery_timeline
  return (
    <div className="card">
      <div className="card-title">Recovered Revenue Timeline</div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data}>
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

function LiveFeed({ records, onSimulate, simulating }) {
  return (
    <div className="card">
      <div className="card-title">Live Recovery Feed</div>
      {!simulating && records.length === 0 ? (
        <div className="empty">
          No events yet.
          <br />
          <br />
          <button className="simulate-btn" onClick={() => onSimulate(25)}>
            ⚡ Stream failure events
          </button>
        </div>
      ) : (
        <div className="feed">
          {records.map((r) => (
            <FeedItem key={r.transaction_id + r.recovered} record={r} />
          ))}
          {simulating && <div style={{ color: 'var(--text-dim)', fontSize: 12, textAlign: 'center', padding: 12 }}>Processing…</div>}
        </div>
      )}
    </div>
  )
}

function FeedItem({ record }) {
  return (
    <div className="feed-item">
      <div className="feed-row">
        <span className="feed-txn">#{record.transaction_id.slice(-8)}</span>
        <span className="feed-amount">{formatINR(record.amount)}</span>
      </div>
      <div className="feed-meta">
        <span className="chip reason">{record.reason_label}</span>
        <span className="chip timing">{TIMING_LABELS[record.retry_timing] || record.retry_timing}</span>
        <span className="chip bank">{record.bank}</span>
        <span className={`chip ${record.recovered ? 'recovered' : 'failed'}`}>
          {record.recovered ? 'Recovered' : 'Lost'}
        </span>
      </div>
      <div className="feed-message">“{record.message}”</div>
    </div>
  )
}

export default App