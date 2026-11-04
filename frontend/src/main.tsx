import { StrictMode, useEffect, useRef, useState, type FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import demo from '../examples/demo-pass.csv?raw'
import './style.css'

type Well = { well: string; role: 'positive' | 'negative' | 'sample'; sample: string; signal: number; inhibition: number }
type Summary = { id: string; name: string; created_at: string; well_count: number; z_prime: number; passed: boolean }
type Run = Summary & { sha256: string; wells: Well[] }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status})`)
  }
  return response.json()
}

function App() {
  const [runs, setRuns] = useState<Summary[]>([])
  const [run, setRun] = useState<Run>()
  const [selected, setSelected] = useState<Well>()
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(true)
  const fileInput = useRef<HTMLInputElement>(null)
  const [name, setName] = useState('')

  function show(next: Run) {
    setRun(next)
    setSelected(undefined)
    setQuery('')
  }

  useEffect(() => {
    const controller = new AbortController()
    const options = { signal: controller.signal }
    async function load() {
      try {
        const items = await api<Summary[]>('/runs', options)
        const latest = items[0] && await api<Run>(`/runs/${items[0].id}`, options)
        if (!controller.signal.aborted) {
          setRuns(items)
          if (latest) show(latest)
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err))
      } finally {
        if (!controller.signal.aborted) setBusy(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [])

  async function perform(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function upload(body: File | string, label: string) {
    void perform(async () => {
      const next = await api<Run>(`/runs?name=${encodeURIComponent(label)}`, {
        method: 'POST', headers: { 'Content-Type': 'text/csv' }, body,
      })
      show(next)
      setStatus(`Opened ${next.name}.`)
      if (fileInput.current) fileInput.current.value = ''
      setName('')
      setRuns(await api<Summary[]>('/runs'))
    })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const file = fileInput.current?.files?.[0]
    if (file) upload(file, name.trim() || file.name.slice(0, 100))
  }

  const byWell = new Map(run?.wells.map(well => [well.well, well]))
  const matches = run?.wells.filter(well =>
    `${well.well} ${well.sample} ${well.role}`.toLowerCase().includes(query.trim().toLowerCase())
  ).sort((a, b) => b.inhibition - a.inhibition) ?? []

  return <>
    <header><div className="shell"><p className="eyebrow">DISCOVERY DATA · PERSONAL PROJECT</p>
      <h1>Assay Workbench</h1><p>Import a plate, inspect control quality, and explore compound signals.</p></div></header>
    <main className="shell layout">
      <aside>
        <section className="card"><h2>Import a plate</h2>
          <form onSubmit={submit}>
            <label>Run name (optional)<input value={name} onChange={e => setName(e.target.value)} maxLength={100} /></label>
            <label>Plate CSV<input ref={fileInput} type="file" accept=".csv,text/csv" required /></label>
            <button className="primary" disabled={busy}>Analyse CSV</button>
          </form>
          <button className="secondary" disabled={busy} onClick={() => upload(demo, 'Synthetic screening plate')}>Load demo plate</button>
          <p className="hint">CSV columns: well, role, sample, signal. Use a 96-well layout and at least three wells per control.</p>
        </section>
        <section className="card"><h2>Recent runs</h2>
          {!runs.length && <p className="hint">Saved plates appear here.</p>}
          <ul className="runs">{runs.map(item => <li key={item.id}>
            <button aria-pressed={item.id === run?.id} disabled={busy} onClick={() =>
              void perform(async () => show(await api<Run>(`/runs/${item.id}`)))}>
              <strong>{item.name}</strong><small>{new Date(item.created_at).toLocaleString()} · {item.passed ? 'Pass' : 'Review'}</small>
            </button>
          </li>)}</ul>
        </section>
      </aside>
      <div className="results" aria-busy={busy}>
        {error && <p role="alert" className="error">{error}</p>}
        <p className="status" role="status">{busy ? 'Working…' : status}</p>
        {!run ? <section className="card empty"><h2>No plate selected</h2>
          <p>Load the synthetic demo to see a completed analysis, or import a CSV to analyse your own synthetic data.</p></section> : <>
          <section className="card">
            <div className="section-title"><div><p className="eyebrow">PLATE OVERVIEW</p><h2>{run.name}</h2></div>
              <a href={`/api/runs/${run.id}/export`}>Export results CSV</a></div>
            <div className="metrics">
              <div><span>Control quality</span><strong className={run.passed ? 'pass' : 'review'}>{run.passed ? 'Pass' : 'Needs review'}</strong></div>
              <div><span>Z′ factor</span><strong>{run.z_prime.toFixed(3)}</strong></div>
              <div><span>Populated wells</span><strong>{run.well_count} / 96</strong></div>
            </div>
            <p className="hint">Pass threshold: Z′ ≥ 0.5.{!run.passed && ' Review control variation before interpreting compound results.'}</p>
          </section>
          <section className="card"><h2>Plate map</h2>
            <p className="hint">P = positive control, N = negative control. Sample colours show lower to higher inhibition. Select a populated well to inspect it.</p>
            <div className="scroll"><table className="plate" aria-label="96-well plate">
              <thead><tr><td />{Array.from({ length: 12 }, (_, col) => <th scope="col" key={col}>{col + 1}</th>)}</tr></thead>
              <tbody>{[...'ABCDEFGH'].map(row => <tr key={row}><th scope="row">{row}</th>
                {Array.from({ length: 12 }, (_, col) => {
                  const position = row + String(col + 1).padStart(2, '0')
                  const well = byWell.get(position)
                  const shade = Math.min(3, Math.max(0, Math.floor((well?.inhibition ?? 0) / 25)))
                  return <td key={position}>{well ? <button
                    className={well.role === 'sample' ? `sample shade-${shade}` : well.role}
                    aria-label={`${position}, ${well.sample}, ${well.inhibition}% inhibition`}
                    aria-pressed={selected?.well === position} onClick={() => setSelected(well)}>
                    {well.role === 'sample' ? '•' : well.role[0].toUpperCase()}
                  </button> : <span className="vacant" aria-label="Empty">–</span>}</td>
                })}
              </tr>)}</tbody>
            </table></div>
            {selected && <p role="status"><strong>{selected.well} · {selected.sample}</strong> · Signal {selected.signal} · Inhibition {selected.inhibition}%</p>}
          </section>
          <section className="card"><div className="section-title"><h2>Well results</h2>
            <label>Filter by well, sample or role<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
            <div className="scroll"><table className="data"><thead><tr>{['Well', 'Sample', 'Role', 'Signal', 'Inhibition ↓'].map(label =>
              <th scope="col" key={label}>{label}</th>)}</tr></thead>
              <tbody>{matches.map(well => <tr key={well.well}><td>{well.well}</td><td>{well.sample}</td>
                <td>{well.role}</td><td>{well.signal}</td><td>{well.inhibition}%</td></tr>)}</tbody></table></div>
            {!matches.length && <p>No wells match your filter.</p>}
            <p className="hint hash">Source SHA-256 · {run.sha256}</p>
          </section>
        </>}
      </div>
    </main>
  </>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
