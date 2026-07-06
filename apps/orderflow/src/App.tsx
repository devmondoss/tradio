// App — Orderflow terminal: Flowsurface-style shell (sidebar + configurable,
// resizable pane grid with maximize). No app header.
import { useState } from 'react'
import Sidebar from './layout/Sidebar'
import PaneGrid from './layout/PaneGrid'
import PaneLeaf from './layout/PaneLeaf'
import {
  leaf, nextId, splitLeaf, closeLeaf, updateLeaf, setRatio, findLeaf, setAllSymbols,
  type Node,
} from './layout/tree'

function defaultLayout(): Node {
  // Flowsurface "Layout 2" arrangement:
  //   left column  → heatmap (top) / line (bottom)
  //   middle column→ footprint (top) / candles (bottom)
  //   right column → DOM ladder (full height)
  return {
    kind: 'split', id: nextId(), dir: 'h', ratio: 0.83,
    a: {
      kind: 'split', id: nextId(), dir: 'h', ratio: 0.5,
      a: {
        kind: 'split', id: nextId(), dir: 'v', ratio: 0.55,
        a: leaf('heatmap', 'BTCUSDT'),
        b: leaf('line', 'BTCUSDT', '15'),
      },
      b: {
        kind: 'split', id: nextId(), dir: 'v', ratio: 0.55,
        a: leaf('footprint', 'BTCUSDT', '15'),
        b: leaf('candles', 'BTCUSDT', '15'),
      },
    },
    b: leaf('dom', 'BTCUSDT'),
  }
}

export default function App() {
  const [tree, setTree] = useState<Node>(defaultLayout)
  const [maxId, setMaxId] = useState<string | null>(null)
  const canClose = tree.kind === 'split'

  const handlers = {
    canClose,
    onUpdate: (id: string, patch: Partial<import('./layout/tree').LeafNode>) =>
      setTree((t) => updateLeaf(t, id, patch)),
    onSplit: (id: string, dir: import('./layout/tree').Dir) => setTree((t) => splitLeaf(t, id, dir)),
    onClose: (id: string) => setTree((t) => closeLeaf(t, id)),
    onRatio: (id: string, ratio: number) => setTree((t) => setRatio(t, id, ratio)),
    onMax: (id: string) => setMaxId((m) => (m === id ? null : id)),
  }

  const maxLeaf = maxId ? findLeaf(tree, maxId) : null

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', background: 'var(--bg)', overflow: 'hidden' }}>
      <Sidebar
        onAddPane={() => setTree((t) => (t.kind === 'leaf' ? splitLeaf(t, t.id, 'h') : t))}
        onGlobalSymbol={(sym) => setTree((t) => setAllSymbols(t, sym))}
        onResetLayout={() => { setTree(defaultLayout()); setMaxId(null) }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        {maxLeaf ? (
          <PaneLeaf
            node={maxLeaf}
            canClose={canClose}
            isMax
            onUpdate={(p) => handlers.onUpdate(maxLeaf.id, p)}
            onSplit={() => handlers.onSplit(maxLeaf.id, 'h')}
            onClose={() => { handlers.onClose(maxLeaf.id); setMaxId(null) }}
            onToggleMax={() => setMaxId(null)}
          />
        ) : (
          <PaneGrid node={tree} {...handlers} />
        )}
      </div>
    </div>
  )
}
