// confluence.ts — Live orderflow confluence detector (ported from the Rust engine).
// Fires when three microstructure conditions converge at the same price:
//   1. WHERE  — price at a volume-profile level (POC / value-area edge)
//   2. WALL   — a resting order-book wall defends it and is NOT a spoof (persisted)
//   3. WHEN   — footprint absorption: one-sided aggressive flow, price holds
// It only analyses; it never trades. Returns a Signal to alert on.

import type { BybitTrade, BybitBook } from './bybitWs'
import { computeVP, type VolumeProfile } from './volumeProfile'

// ── tuning (mirror of the validated Rust defaults) ──────────────────────────
const VP_WINDOW_MS = 30 * 60 * 1000 // rolling VP window (30 min)
const RECENT_DELTA_MS = 12_000 // absorption look-back
const WALL_MULT = 5.0 // wall ≥ 5× median nearby book size
const WALL_PERSIST_MS = 5_000 // anti-spoof: wall must rest this long
const WALL_NEAR_FRAC = 0.002 // walls within 0.20% of price
const MEDIAN_NEAR_FRAC = 0.005 // median book size over ±0.50% of mid
const ABSORB_RATIO = 2.5 // aggressive side ≥ 2.5× the passive side
const ABSORB_VS_WALL = 1.0 // aggressive flow ≥ the full wall it hit
const LEVEL_TOL_FRAC = 0.0008 // "near a VP level" (~0.08%)
const COOLDOWN_MS = 45_000 // per-detector alert cooldown
const WARMUP_TRADES = 300 // trades before the VP is trusted

export interface Signal {
  side: 'long' | 'short'
  price: number
  level: 'POC' | 'VAH' | 'VAL'
  wallQty: number
  wallAgeS: number
  absorbed: number
  ts: number
  text: string
}

interface WallInfo {
  price: number
  qty: number
  since: number | null
}

export class ConfluenceDetector {
  private trades: BybitTrade[] = []
  private totalTrades = 0
  private lastPrice = 0
  private wallSeen = new Map<number, number>() // price-cents → first seen (ms)
  private bidWall: WallInfo = { price: 0, qty: 0, since: null }
  private askWall: WallInfo = { price: 0, qty: 0, since: null }
  private lastAlert = 0
  private symbol: string

  constructor(symbol: string) {
    this.symbol = symbol
  }

  /** Feed the latest order book; refreshes non-spoof wall state. */
  onBook(book: BybitBook, now: number) {
    const bestBid = book.bids[0]?.price
    const bestAsk = book.asks[0]?.price
    if (bestBid == null || bestAsk == null) return
    const mid = (bestBid + bestAsk) / 2

    const near: number[] = []
    for (const l of book.bids) if (Math.abs(l.price - mid) <= mid * MEDIAN_NEAR_FRAC) near.push(l.qty)
    for (const l of book.asks) if (Math.abs(l.price - mid) <= mid * MEDIAN_NEAR_FRAC) near.push(l.qty)
    if (near.length < 5) return
    near.sort((a, b) => a - b)
    const median = Math.max(near[Math.floor(near.length / 2)], 1e-9)
    const thresh = WALL_MULT * median

    const present = new Map<number, number>()
    let nb: BookLevel | null = null
    let na: BookLevel | null = null
    for (const l of book.bids) {
      if (l.qty < thresh || mid - l.price > mid * WALL_NEAR_FRAC || l.price > mid) continue
      const cent = Math.round(l.price * 100)
      const since = this.wallSeen.get(cent) ?? now
      present.set(cent, since)
      if (!nb || l.price > nb.price) { nb = l; this.bidWall.since = since }
    }
    for (const l of book.asks) {
      if (l.qty < thresh || l.price - mid > mid * WALL_NEAR_FRAC || l.price < mid) continue
      const cent = Math.round(l.price * 100)
      const since = this.wallSeen.get(cent) ?? now
      present.set(cent, since)
      if (!na || l.price < na.price) { na = l; this.askWall.since = since }
    }
    this.wallSeen = present
    if (nb) { this.bidWall.price = nb.price; this.bidWall.qty = nb.qty } else { this.bidWall.qty = 0; this.bidWall.since = null }
    if (na) { this.askWall.price = na.price; this.askWall.qty = na.qty } else { this.askWall.qty = 0; this.askWall.since = null }
  }

  /** Feed a trade; returns a Signal when confluence fires (else null). */
  onTrade(t: BybitTrade): Signal | null {
    this.trades.push(t)
    this.totalTrades++
    this.lastPrice = t.price
    const cutoff = t.ts - VP_WINDOW_MS
    while (this.trades.length && this.trades[0].ts < cutoff) this.trades.shift()
    return this.evaluate(t.ts)
  }

  currentWalls() {
    return { bid: this.bidWall, ask: this.askWall, persist: WALL_PERSIST_MS }
  }

  vp(): VolumeProfile | null {
    return computeVP(this.trades)
  }

  private recentFlow(now: number): { buy: number; sell: number } {
    let buy = 0, sell = 0
    for (let i = this.trades.length - 1; i >= 0; i--) {
      const tr = this.trades[i]
      if (now - tr.ts > RECENT_DELTA_MS) break
      if (tr.side === 'Sell') sell += tr.qty
      else buy += tr.qty
    }
    return { buy, sell }
  }

  private evaluate(now: number): Signal | null {
    if (now - this.lastAlert < COOLDOWN_MS) return null
    if (this.totalTrades < WARMUP_TRADES) return null
    const vp = computeVP(this.trades)
    if (!vp) return null
    const price = this.lastPrice
    if (price <= 0) return null
    const { buy, sell } = this.recentFlow(now)
    const tol = price * LEVEL_TOL_FRAC
    const persisted = (since: number | null) => since != null && now - since >= WALL_PERSIST_MS

    // LONG: absorption at support (bid wall soaks aggressive selling)
    const nearSupport = Math.abs(price - vp.poc) <= tol || Math.abs(price - vp.val) <= tol
    if (
      nearSupport &&
      this.bidWall.qty > 0 &&
      persisted(this.bidWall.since) &&
      price >= this.bidWall.price &&
      sell >= ABSORB_RATIO * Math.max(buy, 1e-9) &&
      sell >= ABSORB_VS_WALL * this.bidWall.qty
    ) {
      this.lastAlert = now
      const level = Math.abs(price - vp.val) <= tol ? 'VAL' : 'POC'
      return this.mk('long', price, level, this.bidWall, sell, now)
    }

    // SHORT: absorption at resistance (ask wall soaks aggressive buying)
    const nearResist = Math.abs(price - vp.poc) <= tol || Math.abs(price - vp.vah) <= tol
    if (
      nearResist &&
      this.askWall.qty > 0 &&
      persisted(this.askWall.since) &&
      price <= this.askWall.price &&
      buy >= ABSORB_RATIO * Math.max(sell, 1e-9) &&
      buy >= ABSORB_VS_WALL * this.askWall.qty
    ) {
      this.lastAlert = now
      const level = Math.abs(price - vp.vah) <= tol ? 'VAH' : 'POC'
      return this.mk('short', price, level, this.askWall, buy, now)
    }
    return null
  }

  private mk(
    side: 'long' | 'short',
    price: number,
    level: 'POC' | 'VAH' | 'VAL',
    wall: WallInfo,
    absorbed: number,
    now: number,
  ): Signal {
    const ageS = wall.since ? (now - wall.since) / 1000 : 0
    const sideTxt = side === 'long' ? '🟢 LONG' : '🔴 SHORT'
    const wallTxt = side === 'long' ? 'bid' : 'ask'
    return {
      side,
      price,
      level,
      wallQty: wall.qty,
      wallAgeS: ageS,
      absorbed,
      ts: now,
      text: `${sideTxt} absorción · ${this.symbol} en ${level} ${price.toFixed(1)} · pared ${wallTxt} ${wall.qty.toFixed(1)} (${ageS.toFixed(0)}s) · ${absorbed.toFixed(1)} absorbido, precio aguanta`,
    }
  }
}

// Local alias so we don't import BookLevel separately.
type BookLevel = { price: number; qty: number }
