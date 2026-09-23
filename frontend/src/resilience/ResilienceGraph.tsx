import { useEffect, useRef } from 'react'
import cytoscape, { type ElementDefinition, type StylesheetStyle } from 'cytoscape'

/** core — узлы, вокруг которых строится показ (при N=0 это будущие кандидаты, ещё не удалённые). */
export interface ViewNode { gid: string; removed: boolean; core: boolean; fragment: number | null; label: string }
export interface ViewEdge { src: string; dst: string; cut: boolean }
interface Props { nodes: ViewNode[]; edges: ViewEdge[]; onSelect: (gid: string) => void }

/** Цвет фрагмента: шесть крупнейших различимы, остальные — нейтральные. */
const PALETTE = ['#83d9bd', '#8eaff2', '#edb97c', '#be9df2', '#b6f3cf', '#efbd7c']

export default function ResilienceGraph({ nodes, edges, onSelect }: Props) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    const elements: ElementDefinition[] = [
      ...nodes.map(n => ({
        data: {
          id: n.gid, label: n.label, removed: n.removed ? 1 : 0,
          // кольца: удалённые в центре, отколовшиеся фрагменты — средний круг, основная сеть — снаружи
          ring: n.core ? 3 : n.fragment === 0 ? 1 : 2,
          color: n.removed ? '#ed95a9' : n.fragment !== null && n.fragment < PALETTE.length ? PALETTE[n.fragment] : '#5d6b80',
        },
      })),
      ...edges.map((e, i) => ({ data: { id: `r-${i}`, source: e.src, target: e.dst, cut: e.cut ? 1 : 0 } })),
    ]
    const style: StylesheetStyle[] = [
      { selector: 'node', style: {
        'background-color': 'data(color)', width: 14, height: 14, label: 'data(label)', color: '#c2cad6',
        'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 5, 'min-zoomed-font-size': 7,
        'text-outline-width': 2, 'text-outline-color': '#0e131b',
      } },
      { selector: 'node[removed = 1]', style: {
        shape: 'diamond', width: 20, height: 20, 'border-width': 2, 'border-color': '#eff2f6',
      } },
      { selector: 'edge', style: {
        width: 1, 'line-color': '#6b7c96', 'target-arrow-color': '#6b7c96', 'target-arrow-shape': 'triangle',
        'arrow-scale': 0.6, 'curve-style': 'bezier', opacity: 0.45,
      } },
      { selector: 'edge[cut = 1]', style: {
        'line-style': 'dashed', 'line-color': '#ed95a9', 'target-arrow-color': '#ed95a9', opacity: 0.35,
      } },
    ]
    const cy = cytoscape({
      container: host.current, elements, style, minZoom: 0.05, maxZoom: 3, wheelSensitivity: 0.22,
      // Детерминированная раскладка: удалённые узлы в центре, остальные кольцами.
      layout: { name: 'concentric', animate: false, fit: true, padding: 30, minNodeSpacing: 10,
                concentric: node => node.data('ring'), levelWidth: () => 1 },
    })
    cy.on('tap', 'node', event => onSelect(event.target.id()))
    return () => cy.destroy()
  }, [nodes, edges, onSelect])
  return <div ref={host} className="resilience-canvas" role="img"
    aria-label={`Окрестность удалённых узлов: ${nodes.length} узлов, ${edges.length} связей`} />
}
