import type { Trade } from '../lib/types'
import { sesLabel, fmtR, winRate, avgR } from '../lib/utils'
import { ACCOUNT } from '../lib/types'

interface Props { trades: Trade[] }

const REASON_ORDER = ['TAKE_PROFIT', 'TRAILING_STOP', 'TARGET', 'STOP_LOSS', 'STOP', 'SESSION_END', 'TIME_STOP']
const REASON_LABEL: Record<string, string> = {
  TAKE_PROFIT: 'Take Profit', TRAILING_STOP: 'Trail Stop',
  STOP_LOSS: 'Stop Loss', TARGET: 'Target', STOP: 'Stop',
  SESSION_END: 'Session End', TIME_STOP: 'Time Stop',
}
const REASON_COLOR: Record<string, string> = {
  TAKE_PROFIT: 'var(--green)', TRAILING_STOP: 'var(--blue)',
  TARGET: 'var(--green)', STOP_LOSS: 'var(--red)', STOP: 'var(--red)',
}

function rColor(v: number) { return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text3)' }
function dotColor(t: Trade) { return REASON_COLOR[t.reason ?? ''] ?? ((t.resultR ?? 0) > 0 ? 'var(--green)' : 'var(--red)') }

// ── Hero KPIs ─────────────────────────────────────────────────────────────────

function HeroKpis({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen)
  const final  = closed.length ? closed[closed.length - 1].equity : ACCOUNT
  const gain   = final - ACCOUNT
  const pct    = (gain / ACCOUNT) * 100
  const wr     = winRate(trades)
  const ar     = avgR(trades)
  const totalR = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const wins   = closed.filter(t => (t.resultR ?? 0) > 0).length

  let peak = ACCOUNT, maxDD = 0
  for (const t of closed) {
    if (t.equity > peak) peak = t.equity
    const dd = (peak - t.equity) / peak * 100
    if (dd > maxDD) maxDD = dd
  }

  const col  = gain >= 0 ? 'var(--green)' : 'var(--red)'
  const s    = (v: number) => v >= 0 ? '+' : ''
  const cards = [
    { label: 'Equity Final',  value: `$${final.toFixed(2)}`,               sub: `${s(pct)}${pct.toFixed(2)}% retorno`,              color: col,           accent: col },
    { label: 'Total R',       value: `${s(totalR)}${totalR.toFixed(2)}R`,  sub: `${closed.length} trades · avg ${s(ar)}${ar.toFixed(2)}R`, color: rColor(totalR), accent: rColor(totalR) },
    { label: 'Win Rate',      value: closed.length ? `${wr.toFixed(0)}%` : '—', sub: `${wins} wins / ${closed.length - wins} losses`, color: wr>=55?'var(--green)':wr>=45?'var(--yellow)':'var(--red)', accent: wr>=55?'var(--green)':wr>=45?'var(--yellow)':'var(--red)' },
    { label: 'Max Drawdown',  value: maxDD > 0 ? `-${maxDD.toFixed(1)}%` : '—', sub: `Avg R/trade ${s(ar)}${ar.toFixed(3)}R`,       color: maxDD>15?'var(--red)':maxDD>8?'var(--yellow)':'var(--green)', accent: maxDD>15?'var(--red)':maxDD>8?'var(--yellow)':'var(--green)' },
  ]

  return (
    <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', flexShrink:0, borderBottom:'1px solid var(--border)', background:'var(--bg2)' }}>
      {cards.map((k, i) => (
        <div key={i} style={{ borderRight: i<3 ? '1px solid var(--border)' : 'none', display:'flex', flexDirection:'column' }}>
          <div style={{ height:3, background:k.accent, flexShrink:0 }}/>
          <div style={{ padding:'12px 18px 14px', display:'flex', flexDirection:'column', gap:5 }}>
            <span style={{ fontSize:9, fontWeight:600, textTransform:'uppercase', letterSpacing:1.5, color:'var(--text3)' }}>{k.label}</span>
            <span style={{ fontSize:24, fontWeight:700, fontFamily:'var(--mono)', letterSpacing:-1, color:k.color, lineHeight:1 }}>{k.value}</span>
            <span style={{ fontSize:10, color:'var(--text3)' }}>{k.sub}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Equity curve ──────────────────────────────────────────────────────────────

function EquityChart({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen)
  const pts    = [ACCOUNT, ...closed.map(t => t.equity)]
  if (pts.length < 2) return <div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:'var(--text3)', fontSize:11 }}>Sin trades</div>

  const W = 960, H = 160, RPad = 52, TPad = 8, BPad = 6
  const cW = W - RPad, cH = H - TPad - BPad
  const min = Math.min(...pts), max = Math.max(...pts)
  const pad = (max - min) * 0.12 || 10
  const lo  = min - pad, hi = max + pad
  const xp  = (i: number) => (i / (pts.length - 1)) * cW
  const yp  = (v: number) => TPad + cH - ((v - lo) / (hi - lo)) * cH
  const d   = pts.map((v, i) => `${i===0?'M':'L'}${xp(i).toFixed(1)},${yp(v).toFixed(1)}`).join(' ')
  const area = d + ` L${xp(pts.length-1).toFixed(1)},${H} L0,${H} Z`
  const isUp = pts[pts.length-1] >= ACCOUNT
  const stroke = isUp ? '#22c55e' : '#ef4444'

  // drawdown shading
  let peak = pts[0], ddSegs: {x1:number;x2:number;y1:number;y2:number}[] = []
  let inDD=false, ddStart=0, ddPeak=pts[0], ddMin=pts[0]
  for (let i=1; i<pts.length; i++) {
    if (pts[i] < peak) { if(!inDD){inDD=true;ddStart=i-1;ddPeak=peak;ddMin=pts[i]}else if(pts[i]<ddMin)ddMin=pts[i] }
    else { if(inDD){ddSegs.push({x1:xp(ddStart),x2:xp(i),y1:yp(ddPeak),y2:yp(ddMin)});inDD=false} peak=pts[i] }
  }
  if(inDD) ddSegs.push({x1:xp(ddStart),x2:xp(pts.length-1),y1:yp(ddPeak),y2:yp(ddMin)})

  // grid
  const range = hi-lo
  const step  = range>500?200:range>100?50:range>20?10:5
  const gridVs = Array.from({length:Math.ceil(range/step)+2},(_,i)=>Math.floor(lo/step)*step+i*step).filter(v=>v>lo&&v<hi)

  return (
    <div style={{ flex:1, display:'flex', flexDirection:'column', minHeight:0 }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'8px 16px 5px', flexShrink:0 }}>
        <span style={{ fontSize:8.5, fontWeight:700, textTransform:'uppercase', letterSpacing:1.5, color:'var(--text3)' }}>Equity Curve</span>
        <span style={{ fontSize:10, color:'var(--text3)', fontFamily:'var(--mono)' }}>
          ${ACCOUNT} → <span style={{ color:stroke, fontWeight:700 }}>${pts[pts.length-1].toFixed(2)}</span>
        </span>
      </div>
      <div style={{ flex:1, margin:'0 14px 10px', minHeight:0, borderRadius:8, border:'1px solid var(--border)', background:'var(--bg)', overflow:'hidden', position:'relative' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" style={{ display:'block', position:'absolute', inset:0 }}>
          <defs>
            <linearGradient id="eq-g" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={stroke} stopOpacity="0.18"/>
              <stop offset="100%" stopColor={stroke} stopOpacity="0"/>
            </linearGradient>
          </defs>
          {gridVs.map(v => (
            <g key={v}>
              <line x1={0} y1={yp(v)} x2={cW} y2={yp(v)} stroke="var(--border)" strokeWidth={1}/>
              <text x={cW+6} y={yp(v)+4} fontSize={13} fill="var(--text3)" fontFamily="var(--mono)">${v.toFixed(0)}</text>
            </g>
          ))}
          {ddSegs.map((s,i) => (
            <rect key={i} x={s.x1} y={s.y1} width={Math.max(s.x2-s.x1,1)} height={s.y2-s.y1} fill="#ef4444" fillOpacity="0.07"/>
          ))}
          <line x1={0} y1={yp(ACCOUNT)} x2={cW} y2={yp(ACCOUNT)} stroke="var(--blue)" strokeWidth={1} strokeDasharray="6,4" strokeOpacity="0.5"/>
          <text x={cW+6} y={yp(ACCOUNT)+4} fontSize={13} fill="var(--blue)" fontFamily="var(--mono)" fillOpacity="0.7">${ACCOUNT}</text>
          <path d={area} fill="url(#eq-g)"/>
          <path d={d} fill="none" stroke={stroke} strokeWidth={2.5} strokeLinejoin="round"/>
          {pts.map((v,i) => {
            if(i>0 && i<pts.length-1 && i%Math.max(1,Math.floor(pts.length/60))!==0) return null
            return <circle key={i} cx={xp(i)} cy={yp(v)} r={i===pts.length-1?5:2} fill={v>=ACCOUNT?'#22c55e':'#ef4444'} fillOpacity={i===pts.length-1?1:0.7} stroke={i===pts.length-1?'var(--bg2)':'none'} strokeWidth={2}/>
          })}
        </svg>
      </div>
    </div>
  )
}

// ── R Distribution ────────────────────────────────────────────────────────────

function RDist({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen && t.resultR != null)
  if (!closed.length) return null

  const rs   = closed.map(t => t.resultR ?? 0)
  const minR = Math.min(...rs, -1.2)
  const maxR = Math.max(...rs, 2.2)
  const W=1000, DH=60, AH=18, PAD=70
  const xOf = (r: number) => PAD + ((r-minR)/(maxR-minR))*(W-PAD*2)
  const ticks = [-1,0,1,1.5,2,2.5,3].filter(v=>v>=minR&&v<=maxR)

  const nBins=40, binW=(maxR-minR)/nBins
  const bins = Array.from({length:nBins},(_,i)=>{
    const lo=minR+i*binW, hi=lo+binW
    const hits=closed.filter(t=>{const r=t.resultR??0;return r>=lo&&r<hi})
    return{lo,hi,n:hits.length,wins:hits.filter(t=>(t.resultR??0)>0).length}
  })
  const maxBin=Math.max(...bins.map(b=>b.n),1)
  const byReason=REASON_ORDER.map(r=>({r,trades:closed.filter(t=>t.reason===r)})).filter(g=>g.trades.length)

  return (
    <div style={{ flexShrink:0, padding:'0 14px 10px' }}>
      <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:6, flexWrap:'wrap' }}>
        <span style={{ fontSize:9, fontWeight:600, textTransform:'uppercase', letterSpacing:1.5, color:'var(--text3)' }}>Distribución R</span>
        {byReason.map(({r,trades:g})=>{
          const col=REASON_COLOR[r]??'var(--text3)'
          const tot=g.reduce((s,t)=>s+(t.resultR??0),0)
          return(
            <div key={r} style={{ display:'flex', alignItems:'center', gap:4, fontSize:9 }}>
              <div style={{ width:6,height:6,borderRadius:'50%',background:col,flexShrink:0 }}/>
              <span style={{ color:'var(--text2)' }}>{REASON_LABEL[r]}</span>
              <span style={{ color:'var(--text3)' }}>n={g.length}</span>
              <span style={{ color:col, fontFamily:'var(--mono)' }}>{tot>0?'+':''}{(tot/g.length).toFixed(2)}R</span>
            </div>
          )
        })}
      </div>
      <div style={{ borderRadius:8, border:'1px solid var(--border)', background:'var(--bg)', overflow:'hidden', padding:'8px 6px 2px' }}>
        <svg viewBox={`0 0 ${W} ${DH+AH}`} width="100%" height={DH+AH} style={{ display:'block', overflow:'visible' }}>
          {bins.map((b,i)=>{
            if(!b.n) return null
            const bx=xOf(b.lo),bx2=xOf(b.hi),bh=(b.n/maxBin)*(DH-8)
            return <rect key={i} x={bx} y={DH-8-bh} width={Math.max(bx2-bx-1,1)} height={bh} fill={b.wins/b.n>=0.5?'var(--green)':'var(--red)'} fillOpacity={0.13} rx={2}/>
          })}
          <line x1={xOf(0)} y1={0} x2={xOf(0)} y2={DH} stroke="var(--text3)" strokeWidth={1} strokeDasharray="3,3" strokeOpacity={0.5}/>
          {closed.map((t,i)=>{
            const r=t.resultR??0
            const same=closed.filter(u=>Math.abs((u.resultR??0)-r)<0.04)
            const pos=same.indexOf(t)
            return <circle key={i} cx={xOf(r)} cy={4+(pos%5)*10} r={4} fill={dotColor(t)} fillOpacity={0.88} stroke="var(--bg)" strokeWidth={0.8}/>
          })}
          {ticks.map(v=>(
            <g key={v}>
              <line x1={xOf(v)} y1={DH} x2={xOf(v)} y2={DH+4} stroke="var(--border2)" strokeWidth={1}/>
              <text x={xOf(v)} y={DH+AH-2} textAnchor="middle" fontSize={14} fill="var(--text3)" fontFamily="var(--mono)">{v===0?'0':v>0?`+${v}R`:`${v}R`}</text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  )
}

// ── Breakdown table ───────────────────────────────────────────────────────────

function BTable({ title, groups, showTotal }: {
  title: string
  groups: { label: string; trades: Trade[] }[]
  showTotal?: boolean
}) {
  const all = groups.flatMap(g => g.trades)

  return (
    <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 }}>
      <div style={{ padding:'5px 12px', background:'var(--bg2)', borderBottom:'1px solid var(--border)', fontSize:8, fontWeight:700, textTransform:'uppercase', letterSpacing:1.5, color:'var(--text3)', flexShrink:0 }}>
        {title}
      </div>
      <div style={{ flex:1, overflow:'hidden', minHeight:0 }}>
        <table style={{ width:'100%', borderCollapse:'collapse', tableLayout:'fixed' }}>
          <colgroup>
            <col style={{ width:'35%' }}/>
            <col style={{ width:'10%' }}/>
            <col style={{ width:'33%' }}/>
            <col style={{ width:'22%' }}/>
          </colgroup>
          <thead>
            <tr style={{ background:'var(--bg2)' }}>
              {[['','left'],['N','right'],['WR%','left'],['TotR','right']].map(([h,a],i)=>(
                <th key={i} style={{ padding:'4px 10px', fontSize:8, fontWeight:600, textTransform:'uppercase', letterSpacing:.5, color:'var(--text3)', textAlign:a as any, borderBottom:'1px solid var(--border)', whiteSpace:'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map(g => <BRow key={g.label} label={g.label} trades={g.trades}/>)}
            {showTotal && <BRow label="TOTAL" trades={all} isTotal/>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function BRow({ label, trades, isTotal }: { label:string; trades:Trade[]; isTotal?:boolean }) {
  const closed = trades.filter(t => !t.isOpen)
  const nc     = closed.length
  const wr     = winRate(trades)
  const totR   = closed.reduce((s,t)=>s+(t.resultR??0),0)
  const wrCol  = nc ? (wr>=55?'var(--green)':wr>=45?'var(--yellow)':'var(--red)') : 'var(--text3)'

  return (
    <tr style={{ background:isTotal?'var(--bg3)':'transparent', borderBottom:'1px solid var(--border)' }}>
      <td style={{ padding:'5px 10px', fontSize:10.5, color:isTotal?'var(--text)':'var(--text2)', fontWeight:isTotal?700:400, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{label}</td>
      <td style={{ padding:'5px 10px', fontSize:10, color:'var(--text3)', textAlign:'right', fontFamily:'var(--mono)' }}>{trades.length}</td>
      <td style={{ padding:'5px 10px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:7 }}>
          <span style={{ fontSize:10.5, fontFamily:'var(--mono)', fontWeight:600, color:wrCol, flexShrink:0, minWidth:30, textAlign:'right' }}>{nc?`${wr.toFixed(0)}%`:'—'}</span>
          <div style={{ flex:1, height:3, background:'var(--border)', borderRadius:2, overflow:'hidden' }}>
            <div style={{ width:`${nc?Math.min(wr,100):0}%`, height:'100%', background:wrCol, borderRadius:2 }}/>
          </div>
        </div>
      </td>
      <td style={{ padding:'5px 10px', fontSize:10.5, textAlign:'right', fontFamily:'var(--mono)', fontWeight:isTotal?700:600, color:nc?rColor(totR):'var(--text3)' }}>{nc?fmtR(totR):'—'}</td>
    </tr>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function StatsView({ trades }: Props) {
  const sessions = ['London','LondonNyOverlap','NewYork','Asia','SessionEnd']
  const symbols  = [...new Set(trades.map(t => t.sym))].sort()

  const bySes    = sessions.map(s=>({label:sesLabel(s),trades:trades.filter(t=>t.session===s)})).filter(g=>g.trades.length)
  const bySym    = symbols.map(s=>({label:s.replace('USDT',''),trades:trades.filter(t=>t.sym===s)}))
  const byDir    = (['Short','Long'] as const).map(d=>({label:d,trades:trades.filter(t=>t.dir===d)})).filter(g=>g.trades.length)
  const byReason = REASON_ORDER.map(r=>({label:REASON_LABEL[r]??r,trades:trades.filter(t=>t.reason===r)})).filter(g=>g.trades.length)

  return (
    <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0, background:'var(--bg)' }}>

      {/* ── KPI row ─────────────────────────────────────── */}
      <HeroKpis trades={trades}/>

      {/* ── Body ────────────────────────────────────────── */}
      <div style={{ flex:1, display:'flex', overflow:'hidden', minHeight:0 }}>

        {/* Left — chart + distribution */}
        <div style={{ flex:'0 0 60%', display:'flex', flexDirection:'column', overflow:'hidden', borderRight:'1px solid var(--border)', minHeight:0 }}>
          <EquityChart trades={trades}/>
          <RDist trades={trades}/>
        </div>

        {/* Right — 2×2 table grid */}
        <div style={{ flex:1, display:'grid', gridTemplateColumns:'1fr 1fr', gridTemplateRows:'1fr 1fr', overflow:'hidden', minHeight:0 }}>
          <div style={{ borderBottom:'1px solid var(--border)', borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 }}>
            <BTable title="Por Sesión"    groups={bySes}    showTotal/>
          </div>
          <div style={{ borderBottom:'1px solid var(--border)', display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 }}>
            <BTable title="Por Símbolo"   groups={bySym}/>
          </div>
          <div style={{ borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 }}>
            <BTable title="Por Salida"    groups={byReason} showTotal/>
          </div>
          <div style={{ display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 }}>
            <BTable title="Long vs Short" groups={byDir}/>
          </div>
        </div>

      </div>
    </div>
  )
}
