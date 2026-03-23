import { useState, useRef, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
// layout helpers

// ── Types ──

interface ReferralNode {
  id: number
  username?: string
  first_name?: string
  status?: string
  subscription_status?: string
  is_trial?: boolean
  referral_count?: number
  referral_earnings_rubles?: number
  children?: ReferralNode[]
}

interface ReferralGraphProps {
  rootUser: {
    id: number
    username?: string
    first_name?: string
    status?: string
    subscription_status?: string
    is_trial?: boolean
    referral_count?: number
    balance_rubles?: number
  }
  tree: ReferralNode[]
  stats?: {
    total_users?: number
    total_referrers?: number
    total_earnings_rubles?: number
  }
}

// ── Colors ──

type SubType = 'paid_active' | 'trial' | 'paid_expired' | 'trial_expired' | 'top_referrer' | 'active_referrer' | 'no_sub'

function getSubType(node: { subscription_status?: string; is_trial?: boolean; referral_count?: number }): SubType {
  const rc = node.referral_count ?? 0
  if (rc >= 10) return 'top_referrer'
  if (rc >= 3) return 'active_referrer'
  const sub = node.subscription_status
  if (!sub || sub === 'none') return 'no_sub'
  if (sub === 'active' && node.is_trial) return 'trial'
  if (sub === 'active') return 'paid_active'
  if (sub === 'expired' && node.is_trial) return 'trial_expired'
  if (sub === 'expired') return 'paid_expired'
  return 'no_sub'
}

const COLORS: Record<SubType, { fill: string; border: string; glow: string }> = {
  paid_active:    { fill: '#10b981', border: '#34d399', glow: 'rgba(16,185,129,0.4)' },
  trial:          { fill: '#60a5fa', border: '#93c5fd', glow: 'rgba(96,165,250,0.4)' },
  paid_expired:   { fill: '#f472b6', border: '#f9a8d4', glow: 'rgba(244,114,182,0.35)' },
  trial_expired:  { fill: '#fb923c', border: '#fdba74', glow: 'rgba(251,146,60,0.4)' },
  top_referrer:   { fill: '#e879f9', border: '#f0abfc', glow: 'rgba(232,121,249,0.45)' },
  active_referrer:{ fill: '#818cf8', border: '#a5b4fc', glow: 'rgba(129,140,248,0.4)' },
  no_sub:         { fill: '#6b7280', border: '#9ca3af', glow: 'rgba(107,114,128,0.25)' },
}

const LEGEND: { type: SubType; labelKey: string }[] = [
  { type: 'paid_active',     labelKey: 'refLegend.paidActive' },
  { type: 'trial',           labelKey: 'refLegend.trial' },
  { type: 'paid_expired',    labelKey: 'refLegend.paidExpired' },
  { type: 'trial_expired',   labelKey: 'refLegend.trialExpired' },
  { type: 'top_referrer',    labelKey: 'refLegend.topReferrer' },
  { type: 'active_referrer', labelKey: 'refLegend.activeReferrer' },
  { type: 'no_sub',          labelKey: 'refLegend.noSub' },
]

// ── Layout ──

interface LayoutNode {
  id: number
  x: number
  y: number
  r: number
  subType: SubType
  label: string
  count: number
  parentId: number | null
}

function getRadius(rc: number, isRoot: boolean): number {
  if (isRoot) return Math.min(40, 26 + rc * 1.5)
  if (rc >= 10) return 28
  if (rc >= 5) return 22
  if (rc >= 1) return 18
  return 13
}

function layoutTree(root: ReferralGraphProps['rootUser'], children: ReferralNode[]): LayoutNode[] {
  const nodes: LayoutNode[] = []
  const X_GAP = 120
  const Y_GAP = 100

  const rootRc = root.referral_count ?? children.length
  const rootSub = getSubType(root)
  nodes.push({
    id: root.id, x: 0, y: 0,
    r: getRadius(rootRc, true),
    subType: rootSub,
    label: root.username || root.first_name || `#${root.id}`,
    count: rootRc,
    parentId: null,
  })

  interface QItem { node: ReferralNode; parentId: number; depth: number }
  const queue: QItem[] = children.map((c) => ({ node: c, parentId: root.id, depth: 1 }))
  const depthBuckets: Record<number, LayoutNode[]> = {}

  while (queue.length > 0) {
    const { node, parentId, depth } = queue.shift()!
    const rc = node.referral_count ?? (node.children?.length ?? 0)
    const ln: LayoutNode = {
      id: node.id, x: 0, y: depth * Y_GAP,
      r: getRadius(rc, false),
      subType: getSubType(node),
      label: node.username || node.first_name || `#${node.id}`,
      count: rc,
      parentId,
    }
    nodes.push(ln)
    if (!depthBuckets[depth]) depthBuckets[depth] = []
    depthBuckets[depth].push(ln)

    if (node.children) {
      node.children.forEach((c) => queue.push({ node: c, parentId: node.id, depth: depth + 1 }))
    }
  }

  // Center each depth level
  for (const bucket of Object.values(depthBuckets)) {
    const totalW = (bucket.length - 1) * X_GAP
    bucket.forEach((n, i) => { n.x = -totalW / 2 + i * X_GAP })
  }

  return nodes
}

// ── Component ──

export default function ReferralGraph({ rootUser, tree, stats }: ReferralGraphProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })
  const [hovered, setHovered] = useState<number | null>(null)

  const nodes = layoutTree(rootUser, tree)
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))

  // Compute SVG bounds
  const padding = 60
  const minX = Math.min(...nodes.map((n) => n.x - n.r)) - padding
  const maxX = Math.max(...nodes.map((n) => n.x + n.r)) + padding
  const minY = Math.min(...nodes.map((n) => n.y - n.r)) - padding
  const maxY = Math.max(...nodes.map((n) => n.y + n.r)) + padding + 20
  const svgW = maxX - minX
  const svgH = maxY - minY

  const totalNodes = nodes.length
  const totalReferrers = nodes.filter((n) => n.count > 0).length

  // Pan handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('[data-node]')) return
    setDragging(true)
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y })
  }, [pan])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragging) return
    setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y })
  }, [dragging, dragStart])

  const handleMouseUp = useCallback(() => setDragging(false), [])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    setZoom((z) => Math.max(0.3, Math.min(3, z - e.deltaY * 0.001)))
  }, [])

  const fitView = useCallback(() => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }, [])

  // Touch support
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const prevent = (e: WheelEvent) => e.preventDefault()
    el.addEventListener('wheel', prevent, { passive: false })
    return () => el.removeEventListener('wheel', prevent)
  }, [])

  return (
    <div className="relative rounded-xl overflow-hidden border border-[var(--glass-border)] bg-[var(--surface-body)]">
      {/* Canvas */}
      <div
        ref={containerRef}
        className="h-[420px] sm:h-[520px] cursor-grab active:cursor-grabbing select-none overflow-hidden"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        <svg
          width="100%"
          height="100%"
          viewBox={`${minX} ${minY} ${svgW} ${svgH}`}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: 'center center' }}
        >
          {/* Edges */}
          {nodes.filter((n) => n.parentId !== null).map((n) => {
            const parent = nodeMap.get(n.parentId!)
            if (!parent) return null
            const colors = COLORS[n.subType]
            return (
              <line
                key={`edge-${n.parentId}-${n.id}`}
                x1={parent.x} y1={parent.y}
                x2={n.x} y2={n.y}
                stroke={colors.border}
                strokeWidth={1.2}
                strokeOpacity={0.35}
              />
            )
          })}

          {/* Nodes */}
          {nodes.map((n) => {
            const colors = COLORS[n.subType]
            const isHovered = hovered === n.id
            const scale = isHovered ? 1.15 : 1
            return (
              <g
                key={n.id}
                data-node
                className="cursor-pointer"
                transform={`translate(${n.x}, ${n.y}) scale(${scale})`}
                style={{ transition: 'transform 0.15s ease' }}
                onClick={() => navigate(`/bedolaga/customers/${n.id}`)}
                onMouseEnter={() => setHovered(n.id)}
                onMouseLeave={() => setHovered(null)}
              >
                {/* Glow */}
                <circle r={n.r + 6} fill={colors.glow} opacity={isHovered ? 0.7 : 0.4} />
                {/* Main circle */}
                <circle r={n.r} fill={colors.fill} stroke={colors.border} strokeWidth={2} opacity={0.9} />
                {/* Initial */}
                <text
                  textAnchor="middle" dominantBaseline="central"
                  fill="white" fontWeight="700"
                  fontSize={Math.max(9, n.r * 0.7)}
                  style={{ pointerEvents: 'none' }}
                >
                  {n.label.charAt(0).toUpperCase()}
                </text>
                {/* Label below */}
                {n.r >= 16 && (
                  <text
                    y={n.r + 14}
                    textAnchor="middle"
                    fill="#d1d5db"
                    fontSize={10}
                    fontWeight="500"
                    style={{ pointerEvents: 'none' }}
                  >
                    {n.label}{n.count > 0 ? ` | ${n.count}` : ''}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      </div>

      {/* Zoom controls */}
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1 p-1 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)]">
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setZoom((z) => Math.min(3, z + 0.2))}>
          <ZoomIn className="w-3.5 h-3.5 text-dark-200" />
        </Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setZoom((z) => Math.max(0.3, z - 0.2))}>
          <ZoomOut className="w-3.5 h-3.5 text-dark-200" />
        </Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fitView}>
          <Maximize2 className="w-3.5 h-3.5 text-dark-200" />
        </Button>
      </div>

      {/* Stats — bottom left */}
      <div className="absolute bottom-3 left-3 p-2.5 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)] text-xs space-y-1 min-w-[130px]">
        <div className="flex justify-between gap-3">
          <span className="text-dark-400 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refGraphUsers')}</span>
          <span className="font-bold text-dark-100">{stats?.total_users ?? totalNodes}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-dark-400 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refGraphReferrers')}</span>
          <span className="font-bold text-dark-100">{stats?.total_referrers ?? totalReferrers}</span>
        </div>
        {(stats?.total_earnings_rubles ?? 0) > 0 && (
          <div className="flex justify-between gap-3">
            <span className="text-dark-400 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refEarnings')}</span>
            <span className="font-bold text-emerald-400">{stats!.total_earnings_rubles!.toLocaleString()} ₽</span>
          </div>
        )}
      </div>

      {/* Legend — bottom right */}
      <div className="absolute bottom-3 right-3 p-2.5 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)]">
        <p className="text-[10px] text-dark-400 uppercase tracking-wider mb-1.5">{t('bedolaga.customerDetail.refLegendTitle')}</p>
        <div className="space-y-1">
          {LEGEND.map(({ type, labelKey }) => (
            <div key={type} className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: COLORS[type].fill, boxShadow: `0 0 6px ${COLORS[type].glow}` }}
              />
              <span className="text-[10px] text-dark-200">{t(`bedolaga.customerDetail.${labelKey}`)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
