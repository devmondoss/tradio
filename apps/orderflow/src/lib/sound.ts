// sound.ts — global "big trade" audio alert, toggled from the Sidebar icon.
// Tiny module-level store (no context needed for a single boolean flag).

let enabled = false
const listeners = new Set<(v: boolean) => void>()
let ctx: AudioContext | null = null
let lastBeep = 0

export function isSoundEnabled() { return enabled }

export function toggleSound() {
  enabled = !enabled
  listeners.forEach((l) => l(enabled))
  return enabled
}

export function onSoundChange(fn: (v: boolean) => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** Short beep for a big print; throttled so a burst of large trades doesn't spam. */
export function beepBigTrade(buy: boolean) {
  if (!enabled) return
  const now = Date.now()
  if (now - lastBeep < 400) return
  lastBeep = now
  ctx ??= new AudioContext()
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.frequency.value = buy ? 880 : 440
  osc.type = 'sine'
  gain.gain.setValueAtTime(0.001, ctx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.01)
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.16)
  osc.connect(gain).connect(ctx.destination)
  osc.start()
  osc.stop(ctx.currentTime + 0.18)
}
