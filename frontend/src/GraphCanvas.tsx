import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition, type StylesheetStyle } from 'cytoscape'
import type { EdgeRecord, NodeRecord } from './types'
import { clusterColor } from './presentation'
import { edgeKey } from './copilot/graph'
import { flowPositions } from './graphLayout'

export type GraphCommand = { action: 'in' | 'out' | 'fit'; sequence: number }
interface Props {
  nodes: NodeRecord[]
  edges: EdgeRecord[]
  selectedId?: string
  focusId?: string
  focusSequence?: number
  highlightedGids: string[]
  highlightedEdges: string[]
  layoutFocusId?: string
  colorMode: 'role' | 'cluster'
  command: GraphCommand
  onSelect: (gid: string) => void
  onHover: (gid?: string) => void
}

function highlight(cy: Core, selectedId?: string) {
  cy.batch(() => {
    cy.elements().removeClass('dimmed focused neighbor')
    cy.nodes().unselect()
    if (!selectedId) return
    const selected = cy.getElementById(selectedId)
    if (!selected.length) return
    const neighborhood = selected.closedNeighborhood()
    cy.elements().not(neighborhood).addClass('dimmed')
    neighborhood.addClass('neighbor')
    selected.addClass('focused').select()
  })
}

export default function GraphCanvas({
  nodes,
  edges,
  selectedId,
  focusId,
  focusSequence,
  highlightedGids,
  highlightedEdges,
  layoutFocusId,
  colorMode,
  command,
  onSelect,
  onHover,
}: Props) {
  const host = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const selectionRef = useRef(selectedId)
  selectionRef.current = selectedId
  const modeRef = useRef(colorMode)
  modeRef.current = colorMode
  const focusRef = useRef(focusId)
  focusRef.current = focusId

  useEffect(() => {
    if (!host.current) return
    const tokens = getComputedStyle(host.current)
    const token = (name: string) => tokens.getPropertyValue(`--${name}`).trim()
    const elements: ElementDefinition[] = [
      ...nodes.map((node, index) => ({
        data: {
          id: node.gid,
          order: index,
          label: `…${node.gid.slice(-6)}`,
          role: node.role,
          cluster: node.cluster_id,
          color:
            modeRef.current === 'cluster'
              ? clusterColor(node.cluster_id)
              : token(`role-${node.role}`) || token('role-peripheral'),
          priority: node.priority_score,
          seed: node.is_seed ? 1 : 0,
        },
      })),
      ...edges.map((edge, index) => ({
        data: {
          id: `edge-${index}`,
          source: edge.src,
          target: edge.dst,
          claimKey: edgeKey(edge.src, edge.dst),
          width: Math.max(0.7, Math.min(2.4, Math.log10(edge.sum_kzt + 1) / 3)),
        },
      })),
    ]
    const style: StylesheetStyle[] = [
      {
        selector: 'node',
        style: {
          'background-color': 'data(color)',
          label: 'data(label)',
          color: token('soft'),
          'font-family': token('font-mono'),
          'font-size': layoutFocusId ? 12 : 10,
          'min-zoomed-font-size': 5.5,
          'text-valign': 'bottom',
          'text-margin-y': 7,
          'text-outline-width': 2,
          'text-outline-color': token('canvas'),
          width: 'mapData(priority, 0, 1, 13, 40)',
          height: 'mapData(priority, 0, 1, 13, 40)',
          'border-width': 1.5,
          'border-color': token('canvas'),
          'overlay-opacity': 0,
          'transition-property': 'opacity, border-width',
          'transition-duration': window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 0
            : 180,
        },
      },
      {
        selector: 'node[seed = 1]',
        style: { 'border-width': 2, 'border-color': token('gold'), 'border-style': 'double' },
      },
      {
        selector: 'edge',
        style: {
          width: 'data(width)',
          'line-color': token('edge'),
          'target-arrow-color': token('edge'),
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          opacity: 0.3,
          'arrow-scale': 0.7,
        },
      },
      { selector: 'node.dimmed', style: { opacity: 0.34 } },
      { selector: 'edge.dimmed', style: { opacity: 0.07 } },
      {
        selector: 'edge.neighbor',
        style: {
          opacity: 0.8,
          'line-color': token('edge-focus'),
          'target-arrow-color': token('edge-focus'),
          'arrow-scale': 0.9,
          width: 1.6,
        },
      },
      { selector: 'node.neighbor', style: { 'border-width': 2, 'text-opacity': 1 } },
      {
        selector: 'node.copilot-evidence',
        style: {
          opacity: 1, 'border-width': 3, 'border-color': token('accent'),
          'underlay-color': token('accent'), 'underlay-opacity': 0.1, 'underlay-padding': 7,
        },
      },
      {
        selector: 'edge.copilot-evidence',
        style: { 'line-color': token('accent'), 'target-arrow-color': token('accent'), opacity: 1, width: 3, 'z-index': 9 },
      },
      {
        selector: 'node.focused',
        style: {
          'border-width': 3,
          'border-color': token('text'),
          'underlay-color': 'data(color)',
          'underlay-opacity': 0.13,
          'underlay-padding': 13,
          'font-size': 12,
          color: token('text'),
          'min-zoomed-font-size': 0,
          'z-index': 10,
        },
      },
    ]
    const cy = cytoscape({
      container: host.current,
      elements,
      style,
      minZoom: 0.025,
      maxZoom: 4,
      wheelSensitivity: 0.22,
      hideEdgesOnViewport: nodes.length > 250,
      layout: layoutFocusId ? {
        name: 'preset', positions: flowPositions(nodes, edges, layoutFocusId),
        fit: true, padding: 58,
      } : {
        name: 'concentric',
        animate: false,
        fit: true,
        padding: 50,
        minNodeSpacing: 18,
        avoidOverlap: true,
        // Cap ring populations for large views so hundreds of low-degree nodes
        // cannot force a single enormous outer ring.
        concentric: (node) => {
          if (node.id() === selectionRef.current) return 1000
          if (nodes.length > 100) {
            const perRing = Math.ceil(Math.sqrt(nodes.length)) * 2
            return Math.floor((nodes.length - node.data('order')) / perRing)
          }
          return Math.min(20, node.degree(false))
        },
        levelWidth: () => (nodes.length > 100 ? 1 : 4),
        startAngle: -Math.PI / 2,
        sweep: Math.PI * 2 - 0.12,
      },
    })
    cyRef.current = cy
    highlight(cy, selectionRef.current)
    cy.on('tap', 'node', (event) => onSelect(event.target.id()))
    cy.on('mouseover', 'node', (event) => onHover(event.target.id()))
    cy.on('mouseout', 'node', () => onHover(undefined))
    const observer = new ResizeObserver(() => {
      cy.resize()
      const focus = focusRef.current ? cy.getElementById(focusRef.current) : undefined
      // Preserve the complete visible neighborhood, including asymmetric flows.
      // Centering only the client after fit moves distant neighbors off-canvas.
      if (focus?.length) cy.fit(focus.closedNeighborhood(), 58)
      else cy.fit(cy.elements(), layoutFocusId ? 58 : 50)
    })
    observer.observe(host.current)
    return () => {
      observer.disconnect()
      cy.destroy()
      cyRef.current = null
    }
  }, [nodes, edges, layoutFocusId, onSelect, onHover])

  useEffect(() => {
    if (cyRef.current) highlight(cyRef.current, selectedId)
  }, [selectedId])
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.elements().removeClass('copilot-evidence')
      for (const gid of highlightedGids) cy.getElementById(gid).addClass('copilot-evidence')
      const citedEdges = new Set(highlightedEdges)
      cy.edges().filter(edge => citedEdges.has(edge.data('claimKey'))).addClass('copilot-evidence')
    })
  }, [highlightedGids, highlightedEdges, nodes, edges, layoutFocusId])
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !focusId) return
    const node = cy.getElementById(focusId)
    if (node.empty()) return
    cy.stop()
    const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250
    // A fixed zoom cropped large neighborhoods after explicit GID navigation.
    // Fit the observed links as well as the selected client; only navigation
    // issues a camera command, not a new Copilot answer or highlight update.
    cy.animate({ fit: { eles: node.closedNeighborhood(), padding: 58 }, duration })
  }, [focusId, focusSequence])
  useEffect(() => {
    if (!cyRef.current || !host.current) return
    const tokens = getComputedStyle(host.current)
    cyRef.current.batch(() =>
      cyRef.current!.nodes().forEach((node) => {
        node.data(
          'color',
          colorMode === 'cluster'
            ? clusterColor(node.data('cluster'))
            : tokens.getPropertyValue(`--role-${node.data('role')}`).trim() ||
                tokens.getPropertyValue('--role-peripheral').trim(),
        )
      }),
    )
  }, [colorMode])
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !command.sequence) return
    const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 240
    if (command.action === 'fit')
      cy.animate({ fit: { eles: cy.elements(), padding: 42 }, duration })
    else
      cy.animate({
        zoom: Math.max(
          cy.minZoom(),
          Math.min(cy.maxZoom(), cy.zoom() * (command.action === 'in' ? 1.3 : 1 / 1.3)),
        ),
        duration,
      })
  }, [command])

  return (
    <div
      ref={host}
      className="graph-canvas"
      role="img"
      aria-label={`Направленный граф: ${nodes.length} узлов, ${edges.length} связей. Выбор клиента также доступен через поиск и таблицу приоритетов.`}
    />
  )
}
