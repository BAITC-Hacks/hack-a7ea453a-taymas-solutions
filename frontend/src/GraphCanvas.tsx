import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import type { EdgeRecord, NodeRecord } from './types'
import { ROLE_COLORS } from './types'

interface Props {
  nodes: NodeRecord[]
  edges: EdgeRecord[]
  selectedId?: string
  onSelect: (gid: string) => void
  onHover: (gid?: string) => void
}

export default function GraphCanvas({ nodes, edges, selectedId, onSelect, onHover }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)

  useEffect(() => {
    if (!host.current) return undefined
    cyRef.current?.destroy()
    const elements: ElementDefinition[] = [
      ...nodes.map((node) => ({ data: { id: node.gid, label: node.gid.slice(-6), role: node.role, color: ROLE_COLORS[node.role] ?? ROLE_COLORS.peripheral, priority: node.priority_score, seed: node.is_seed } })),
      ...edges.map((edge, index) => ({
        data: { id: `edge-${index}-${edge.src}-${edge.dst}`, source: edge.src, target: edge.dst, label: edge.sum_kzt, width: Math.max(1, Math.min(5, Math.log10(edge.sum_kzt + 1) - 2)), curve: edge.src < edge.dst ? 32 : -32 },
      })),
    ]
    const cy = cytoscape({
      container: host.current, elements, minZoom: 0.2, maxZoom: 4,
      style: [
        { selector: 'node', style: { 'background-color': 'data(color)', label: 'data(label)', color: '#f5f8ff', 'font-size': 9, 'text-outline-width': 2, 'text-outline-color': '#09111f', width: 'mapData(priority, 0, 1, 16, 34)', height: 'mapData(priority, 0, 1, 16, 34)', 'border-width': 1, 'border-color': '#ffffff55', 'overlay-opacity': 0 } },
        { selector: 'node[seed = true]', style: { 'border-width': 3, 'border-color': '#f4c56a' } },
        { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#ffffff', 'overlay-color': '#ffffff', 'overlay-opacity': 0.12 } },
        { selector: 'edge', style: { width: 'data(width)', 'line-color': '#6c7f9d55', 'target-arrow-color': '#90a4c4', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'control-point-distance': 'data(curve)', 'control-point-weight': 0.5, opacity: 0.66, 'arrow-scale': 0.72 } },
        { selector: 'edge:selected', style: { 'line-color': '#ffffff', 'target-arrow-color': '#ffffff', opacity: 1, width: 2.5 } },
      ] as any,
      layout: { name: 'cose', animate: false, fit: true, padding: 44, nodeRepulsion: 7000, idealEdgeLength: 100, edgeElasticity: 80 },
    })
    cy.on('tap', 'node', (event) => onSelect(event.target.id()))
    cy.on('mouseover', 'node', (event) => onHover(event.target.id()))
    cy.on('mouseout', 'node', () => onHover(undefined))
    cyRef.current = cy
    return () => { cy.destroy(); cyRef.current = null }
  }, [nodes, edges, onSelect, onHover])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.nodes().unselect()
    if (selectedId) cy.getElementById(selectedId).select()
  }, [selectedId])

  return <div ref={host} className="graph-canvas" role="img" aria-label="Направленный граф транзакционной сети" />
}
