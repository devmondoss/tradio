// CALIBRACIÓN RBF — Grid search completo sobre todos los parámetros
// Objetivo: encontrar la combinación que da WR >= 55% con n >= 8
import { createClient } from '@supabase/supabase-js'

const sb = createClient(
  'https://ztdhvmcisjjyhbqlgkzm.supabase.co',
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'
)

// ── parámetros fijos ───────────────────────────────────────────────────────────
const CONS_MIN=15, CONS_MAX=60, RANGE_MIN=0.0008, RANGE_MAX=0.0055
const TRAIL_ATR_K=1.2, TRAIL_R=1.5, RR=2.0
const SYM_TABLES = { BTCUSDT:'btc_bars',ETHUSDT:'eth_bars',BNBUSDT:'bnb_bars',SOLUSDT:'sol_bars',XRPUSDT:'xrp_bars' }

async function fetchBars(table, startMs) {
  const all=[]; let from=0
  while(true) {
    const {data,error} = await sb.from(table)
      .select('ts_ms,open,high,low,close,vr,atr,session,cvd_slope,dz,obi_l5,bar_delta,vwap')
      .gte('ts_ms',startMs).order('ts_ms',{ascending:true}).range(from,from+999)
    if(error||!data||!data.length) break
    all.push(...data)
    if(data.length<1000) break
    from+=1000
  }
  return all
}

function simulate(bars, entry, stop, target, atr, timeStop) {
  let bestLow=entry, trailActive=false, trailStop=stop
  for(let k=0;k<bars.length;k++) {
    const b=bars[k], eStop=trailActive?trailStop:stop
    if(b.high>=eStop) return { r:(entry-eStop)/(stop-entry), win:false }
    if(b.low<=target) return { r:RR, win:true }
    if(b.low<bestLow) bestLow=b.low
    if((entry-bestLow)/(stop-entry)>=TRAIL_R) trailActive=true
    if(trailActive&&atr>0){ const c=bestLow+TRAIL_ATR_K*atr; if(c<trailStop) trailStop=c }
    if(k+1>=timeStop) { if(entry-b.close<0) return { r:(entry-b.close)/(stop-entry), win:false } }
  }
  return { r:0, win:false }
}

// Recoge TODOS los trades posibles sin filtros micro — luego filtramos en memoria
function collectAll(sym, bars, timeStop) {
  const trades=[]
  for(let i=CONS_MAX; i<bars.length-timeStop; i++) {
    const bar=bars[i]
    if(!(bar.session==='London'||bar.session==='LondonNyOverlap'||bar.session==='NewYork')) continue
    if((bar.vr??0)<2.5) continue   // VR mínimo bajo para no excluir en el scan
    if((bar.atr??0)<=0) continue
    let found=false
    for(let clen=CONS_MIN; clen<=CONS_MAX&&!found; clen++) {
      const win=bars.slice(i-clen,i)
      const lo=Math.min(...win.map(b=>b.low))
      const hi=Math.max(...win.map(b=>b.high))
      const rangePct=(hi-lo)/lo
      if(rangePct<RANGE_MIN||rangePct>RANGE_MAX||bar.close>=lo) continue

      const cvdInRange=win.every(b=>b.bar_delta!=null)?win.reduce((s,b)=>s+b.bar_delta,0):null
      const ext=(lo-bar.close)/lo
      const dzDir=bar.dz!=null?Math.max(-bar.dz,0):null
      const vswap=bar.vwap!=null&&bar.vwap>0?(bar.close-bar.vwap)/bar.vwap:null
      const touchesLow=win.filter(b=>b.low<=lo*1.001&&b.close>lo).length

      const entry=bar.close, stop=hi, target=entry-RR*(stop-entry)
      const sim=simulate(bars.slice(i+1,i+1+timeStop+30), entry, stop, target, bar.atr, timeStop)

      trades.push({
        sym, session:bar.session,
        r:sim.r, win:sim.win,
        vr:bar.vr, dzDir,
        cvdSlope:bar.cvd_slope, cvdInRange, ext,
        vswap, touchesLow, rangePct, rangeBars:clen,
        obi:bar.obi_l5,
      })
      found=true; i++
    }
  }
  return trades
}

function wr(trades) {
  if(!trades.length) return {n:0,wr:0,avgR:0}
  const w=trades.filter(t=>t.win).length
  const avg=trades.reduce((s,t)=>s+t.r,0)/trades.length
  return {n:trades.length, wr:Math.round(w/trades.length*1000)/10, avgR:Math.round(avg*1000)/1000}
}

// ── main ──────────────────────────────────────────────────────────────────────
const startMs = Date.now() - 30*24*60*60*1000
console.log('\nCargando datos...')
let allTrades=[]
for(const [sym,table] of Object.entries(SYM_TABLES)) {
  process.stdout.write(`  ${sym}... `)
  const bars=await fetchBars(table,startMs)
  // Probar con timeStop=30
  const t=collectAll(sym,bars,30)
  console.log(`${bars.length} bars → ${t.length} trades base`)
  allTrades.push(...t)
}
console.log(`\nTotal trades base (sin filtros): ${allTrades.length}\n`)

// ═══════════════════════════════════════════════════════════════════════════════
// GRID SEARCH
// ═══════════════════════════════════════════════════════════════════════════════
const results=[]

const VR_THRESHOLDS   = [3.0, 3.5, 4.0, 5.0]
const VSWAP_THRESHOLDS = [null, 0.002, 0.0, -0.001, -0.002, -0.003, -0.005]
const EXT_THRESHOLDS  = [0, 0.0005, 0.001, 0.0015, 0.002]
const DZ_RANGES       = [null, [0.5,3.0], [1.0,3.0], [1.5,3.0], [0.5,2.5], [1.0,2.5]]
const CVD_SLOPE       = ['any', 'pos', 'neg']
const CVD_RANGE       = ['any', 'neg']
const SESSIONS        = [
  ['London','LondonNyOverlap','NewYork'],
  ['London','NewYork'],
  ['London'],
  ['NewYork'],
]

for(const vr of VR_THRESHOLDS)
for(const vswap of VSWAP_THRESHOLDS)
for(const ext of EXT_THRESHOLDS)
for(const dz of DZ_RANGES)
for(const cslope of CVD_SLOPE)
for(const crange of CVD_RANGE)
for(const sess of SESSIONS) {

  const filtered = allTrades.filter(t => {
    if(t.vr < vr) return false
    if(vswap !== null) {
      if(t.vswap === null) return false
      if(t.vswap < vswap) return false
    }
    if(ext > 0 && t.ext < ext) return false
    if(dz !== null) {
      if(t.dzDir === null) return false
      if(t.dzDir < dz[0] || t.dzDir > dz[1]) return false
    }
    if(cslope === 'pos' && (t.cvdSlope === null || t.cvdSlope < 0)) return false
    if(cslope === 'neg' && (t.cvdSlope === null || t.cvdSlope >= 0)) return false
    if(crange === 'neg' && (t.cvdInRange === null || t.cvdInRange >= 0)) return false
    if(!sess.includes(t.session)) return false
    return true
  })

  const s=wr(filtered)
  if(s.n>=5 && s.wr>=30) {
    results.push({
      n:s.n, wr:s.wr, avgR:s.avgR,
      params: {
        vr, vswap, ext,
        dz: dz?`[${dz[0]}-${dz[1]}]`:'any',
        cvdSlope:cslope, cvdRange:crange,
        sessions:sess.map(s=>s.replace('LondonNyOverlap','Overlap')).join('+')
      }
    })
  }
}

// Ordenar por WR desc, luego AvgR desc
results.sort((a,b) => b.wr-a.wr || b.avgR-a.avgR)

console.log(`═══ TOP COMBINACIONES (WR>=30%, n>=5) — ${results.length} encontradas ═══\n`)
const shown=new Set()
let rank=0
for(const r of results) {
  // Dedup: no mostrar si misma WR+n+avgR ya vista
  const key=`${r.wr}_${r.n}_${r.avgR}`
  if(shown.has(key)) continue
  shown.add(key)
  rank++
  if(rank>30) break

  const star = r.wr>=60?'🎯':r.wr>=55?'✓✓':r.wr>=50?'✓ ':' '
  console.log(`${star} WR=${String(r.wr).padEnd(5)}% AvgR=${String(r.avgR).padEnd(7)} n=${String(r.n).padEnd(4)}`)
  const p=r.params
  console.log(`   VR>=${p.vr} | VSWAP>${p.vswap??'any'} | ext>${p.ext} | dz=${p.dz} | slope=${p.cvdSlope} | cvdRange=${p.cvdRange} | sess=${p.sessions}`)
  console.log()
}

// Top 3 desglosado por símbolo
console.log('═══ TOP 3 DESGLOSADO POR SÍMBOLO ═══\n')
const top3=results.filter((r,i,a)=>{
  const key=`${r.wr}_${r.n}_${r.avgR}`
  return a.findIndex(x=>`${x.wr}_${x.n}_${x.avgR}`===key)===i
}).slice(0,3)

for(const r of top3) {
  const p=r.params
  console.log(`── VR>=${p.vr} | vswap>${p.vswap??'any'} | ext>${p.ext} | dz=${p.dz} | slope=${p.cvdSlope} | sess=${p.sessions}`)
  console.log(`   GLOBAL: n=${r.n} WR=${r.wr}% AvgR=${r.avgR}`)
  const filtFn = t =>
    t.vr>=p.vr &&
    (p.vswap===null||( t.vswap!=null&&t.vswap>=p.vswap )) &&
    t.ext>=p.ext &&
    (p.dz==='any'||(t.dzDir!=null&&t.dzDir>=parseFloat(p.dz.slice(1))&&t.dzDir<=parseFloat(p.dz.split('-')[1]))) &&
    (p.cvdSlope==='any'||(p.cvdSlope==='pos'?t.cvdSlope>=0:t.cvdSlope<0)) &&
    (p.cvdRange==='any'||(t.cvdInRange!=null&&t.cvdInRange<0)) &&
    p.sessions.split('+').some(s=>t.session.includes(s.replace('Overlap','LondonNyOverlap')))
  for(const sym of Object.keys(SYM_TABLES)) {
    const s=wr(allTrades.filter(t=>t.sym===sym).filter(filtFn))
    if(s.n>0) console.log(`   ${sym.replace('USDT','').padEnd(5)} n=${s.n} WR=${s.wr}% AvgR=${s.avgR}`)
  }
  console.log()
}
