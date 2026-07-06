// tree.ts — pane layout as a binary split tree (Flowsurface's model).
// A leaf is a configurable pane; a split holds two children with a drag ratio.

export type PaneType =
  | 'empty' | 'candles' | 'line' | 'dom'
  | 'footprint' | 'heatmap' | 'timeandsales' | 'comparison'
export type Dir = 'h' | 'v' // horizontal split = side-by-side; vertical = stacked

export interface LeafNode {
  kind: 'leaf'
  id: string
  type: PaneType
  symbol: string
  interval: string
}

export interface SplitNode {
  kind: 'split'
  id: string
  dir: Dir
  ratio: number // fraction for child A (0..1)
  a: Node
  b: Node
}

export type Node = LeafNode | SplitNode

let _id = 0
export const nextId = () => `p${++_id}`

export function leaf(type: PaneType, symbol = 'BTCUSDT', interval = '5'): LeafNode {
  return { kind: 'leaf', id: nextId(), type, symbol, interval }
}

// ── immutable tree operations (by id) ───────────────────────────────────────

/** Replace the leaf `id` with a split of itself + a new empty pane. */
export function splitLeaf(root: Node, id: string, dir: Dir): Node {
  return map(root, (n) => {
    if (n.kind === 'leaf' && n.id === id) {
      return { kind: 'split', id: nextId(), dir, ratio: 0.5, a: n, b: leaf('empty', n.symbol, n.interval) }
    }
    return n
  })
}

/** Remove the leaf `id`; its parent split collapses to the sibling. */
export function closeLeaf(root: Node, id: string): Node {
  if (root.kind === 'leaf') return root // never remove the last pane
  const rec = (n: Node): Node => {
    if (n.kind === 'leaf') return n
    if (n.a.kind === 'leaf' && n.a.id === id) return rec2(n.b)
    if (n.b.kind === 'leaf' && n.b.id === id) return rec2(n.a)
    return { ...n, a: rec(n.a), b: rec(n.b) }
  }
  const rec2 = (n: Node): Node => n // sibling promoted as-is
  return rec(root)
}

/** Patch the leaf `id` with new config (type/symbol/interval). */
export function updateLeaf(root: Node, id: string, patch: Partial<LeafNode>): Node {
  return map(root, (n) => (n.kind === 'leaf' && n.id === id ? { ...n, ...patch } : n))
}

/** Set the drag ratio of the split `id`. */
export function setRatio(root: Node, id: string, ratio: number): Node {
  return map(root, (n) =>
    n.kind === 'split' && n.id === id ? { ...n, ratio: Math.max(0.1, Math.min(0.9, ratio)) } : n,
  )
}

/** Set every leaf's symbol at once (global symbol switch). */
export function setAllSymbols(root: Node, symbol: string): Node {
  return map(root, (n) => (n.kind === 'leaf' && n.type !== 'comparison' ? { ...n, symbol } : n))
}

/** Find a leaf by id (for maximize). */
export function findLeaf(root: Node, id: string): LeafNode | null {
  if (root.kind === 'leaf') return root.id === id ? root : null
  return findLeaf(root.a, id) ?? findLeaf(root.b, id)
}

function map(node: Node, fn: (n: Node) => Node): Node {
  const mapped = fn(node)
  if (mapped.kind === 'split') {
    return { ...mapped, a: map(mapped.a, fn), b: map(mapped.b, fn) }
  }
  return mapped
}
