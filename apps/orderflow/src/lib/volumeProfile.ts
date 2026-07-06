// volumeProfile.ts — rolling volume profile from live trades.
// Bins volume by price and derives POC + 70% value area (VAH/VAL) + HVN/LVN.

import type { BybitTrade } from './bybitWs'

const BIN_FRAC = 0.0002 // price bin = 2 bps of price
const VALUE_AREA_PCT = 0.7

export interface VolumeProfile {
  poc: number
  vah: number
  val: number
  maxVol: number
  levels: { price: number; vol: number }[] // sorted by price
  hvn: number[] // high-volume node prices (acceptance)
  lvn: number[] // low-volume node prices (rejection)
}

export function computeVP(trades: BybitTrade[]): VolumeProfile | null {
  if (trades.length < 2) return null
  const binSize = Math.max(trades[trades.length - 1].price * BIN_FRAC, 1e-9)
  const map = new Map<number, number>()
  for (const t of trades) {
    const bin = Math.round(t.price / binSize)
    map.set(bin, (map.get(bin) ?? 0) + t.qty)
  }
  if (map.size < 2) return null

  const levels = [...map.entries()]
    .map(([bin, vol]) => ({ price: bin * binSize, vol }))
    .sort((a, b) => a.price - b.price)

  const total = levels.reduce((s, l) => s + l.vol, 0)
  let maxVol = 0
  let pocIdx = 0
  levels.forEach((l, i) => {
    if (l.vol > maxVol) { maxVol = l.vol; pocIdx = i }
  })

  // value area: expand outward from POC to 70% of volume
  let lo = pocIdx, hi = pocIdx, acc = levels[pocIdx].vol
  while (acc < VALUE_AREA_PCT * total && (lo > 0 || hi < levels.length - 1)) {
    const up = hi + 1 < levels.length ? levels[hi + 1].vol : -1
    const dn = lo > 0 ? levels[lo - 1].vol : -1
    if (up >= dn) { hi++; acc += Math.max(up, 0) } else { lo--; acc += Math.max(dn, 0) }
  }

  // HVN / LVN — local peaks and troughs
  const hvn: number[] = []
  const lvn: number[] = []
  const w = 2
  for (let i = w; i < levels.length - w; i++) {
    const v = levels[i].vol
    const win = levels.slice(i - w, i + w + 1)
    if (win.every((x) => v >= x.vol) && v >= 0.5 * maxVol) hvn.push(levels[i].price)
    else if (win.every((x) => v <= x.vol) && v <= 0.15 * maxVol) lvn.push(levels[i].price)
  }

  return {
    poc: levels[pocIdx].price,
    vah: levels[hi].price,
    val: levels[lo].price,
    maxVol,
    levels,
    hvn,
    lvn,
  }
}
