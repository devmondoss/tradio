// PaneGrid — recursively renders the pane tree. Splits become flex containers
// with a draggable divider (resize); leaves render a configurable PaneLeaf.
import { useRef, useState } from 'react'
import type { Node, SplitNode, LeafNode, Dir } from './tree'
import PaneLeaf from './PaneLeaf'

interface Handlers {
  canClose: boolean
  onUpdate: (id: string, patch: Partial<LeafNode>) => void
  onSplit: (id: string, dir: Dir) => void
  onClose: (id: string) => void
  onRatio: (id: string, ratio: number) => void
  onMax: (id: string) => void
}

export default function PaneGrid({ node, ...h }: { node: Node } & Handlers) {
  if (node.kind === 'leaf') {
    return (
      <PaneLeaf
        node={node}
        canClose={h.canClose}
        isMax={false}
        onUpdate={(p) => h.onUpdate(node.id, p)}
        onSplit={() => h.onSplit(node.id, 'h')}
        onClose={() => h.onClose(node.id)}
        onToggleMax={() => h.onMax(node.id)}
      />
    )
  }
  return <SplitView node={node} {...h} />
}

function SplitView({ node, ...h }: { node: SplitNode } & Handlers) {
  const ref = useRef<HTMLDivElement>(null)
  const row = node.dir === 'h'
  const [active, setActive] = useState(false)

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault()
    const el = ref.current
    if (!el) return
    setActive(true)
    const move = (ev: PointerEvent) => {
      const r = el.getBoundingClientRect()
      const ratio = row ? (ev.clientX - r.left) / r.width : (ev.clientY - r.top) / r.height
      h.onRatio(node.id, ratio)
    }
    const up = () => {
      setActive(false)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div
      ref={ref}
      style={{ display: 'flex', flexDirection: row ? 'row' : 'column', width: '100%', height: '100%', minWidth: 0, minHeight: 0 }}
    >
      <div style={{ flexBasis: `${node.ratio * 100}%`, flexGrow: 0, flexShrink: 0, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
        <PaneGrid node={node.a} {...h} />
      </div>
      <div
        onPointerDown={startDrag}
        onMouseEnter={() => setActive(true)}
        onMouseLeave={() => setActive(false)}
        style={{
          ...(row ? { width: 10, margin: '0 -2px' } : { height: 10, margin: '-2px 0' }),
          cursor: row ? 'col-resize' : 'row-resize',
          flexShrink: 0, zIndex: 5, position: 'relative',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        <div
          style={{
            ...(row ? { width: 2, height: '100%' } : { height: 2, width: '100%' }),
            background: active ? 'var(--green)' : 'var(--border2)', pointerEvents: 'none', transition: 'background .1s',
          }}
        />
      </div>
      <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
        <PaneGrid node={node.b} {...h} />
      </div>
    </div>
  )
}
