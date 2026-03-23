import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  RefreshCw,
  Search,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Users,
  Share2,
  Megaphone,
  X,
} from 'lucide-react'
import client from '@/api/client'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

// ── Types (matching Bedolaga API) ──

interface NetworkUserNode {
  id: number
  tg_id?: number | null
  username?: string | null
  display_name: string
  is_partner?: boolean
  referrer_id?: number | null
  campaign_id?: number | null
  direct_referrals: number
  total_branch_users?: number
  branch_revenue_kopeks?: number
  personal_revenue_kopeks?: number
  subscription_name?: string | null
  subscription_end?: string | null
}

interface NetworkCampaignNode {
  id: number
  name: string
  is_active?: boolean
  direct_users: number
  total_network_users?: number
  total_revenue_kopeks?: number
}

interface NetworkEdge {
  source: string
  target: string
  type: 'referral' | 'campaign'
}

interface NetworkGraphData {
  users: NetworkUserNode[]
  campaigns?: NetworkCampaignNode[]
  edges: NetworkEdge[]
  total_users: number
  total_referrers: number
  total_campaigns?: number
  total_earnings_kopeks?: number
}

// ── Color by subscription ──

function getNodeColor(user: NetworkUserNode): { fill: string; border: string; glow: string } {
  if (user.is_partner) return { fill: '#fbbf24', border: '#fcd34d', glow: 'rgba(251,191,36,0.35)' }
  if (user.direct_referrals >= 10) return { fill: '#e879f9', border: '#f0abfc', glow: 'rgba(232,121,249,0.4)' }
  if (user.direct_referrals >= 3) return { fill: '#818cf8', border: '#a5b4fc', glow: 'rgba(129,140,248,0.35)' }

  const subEnd = user.subscription_end
  const subName = user.subscription_name
  if (!subName) return { fill: '#6b7280', border: '#9ca3af', glow: 'rgba(107,114,128,0.2)' }

  const expired = subEnd ? new Date(subEnd).getTime() < Date.now() : false
  const isTrial = subName?.toLowerCase().includes('trial') || subName?.toLowerCase().includes('триал')

  if (expired && isTrial) return { fill: '#fb923c', border: '#fdba74', glow: 'rgba(251,146,60,0.35)' }
  if (expired) return { fill: '#f472b6', border: '#f9a8d4', glow: 'rgba(244,114,182,0.3)' }
  if (isTrial) return { fill: '#60a5fa', border: '#93c5fd', glow: 'rgba(96,165,250,0.35)' }
  return { fill: '#10b981', border: '#34d399', glow: 'rgba(16,185,129,0.35)' }
}

function getNodeRadius(referrals: number): number {
  if (referrals >= 20) return 32
  if (referrals >= 10) return 26
  if (referrals >= 5) return 20
  if (referrals >= 1) return 16
  return 10
}

// Campaign color (for future use)
// const CAMPAIGN_COLOR = { fill: '#f472b6', border: '#f9a8d4', glow: 'rgba(244,114,182,0.3)' }

// ── Layout ──

interface LayoutItem {
  id: string
  x: number
  y: number
  r: number
  color: { fill: string; border: string; glow: string }
  label: string
  count: number
  type: 'user' | 'campaign'
  rawId: number
}

function layoutGraph(data: NetworkGraphData): { items: LayoutItem[]; edges: { x1: number; y1: number; x2: number; y2: number; color: string; type: string }[] } {
  const items: LayoutItem[] = []
  const posMap = new Map<string, { x: number; y: number }>()

  const users = data.users || []

  // Build parent→children map
  const childrenMap = new Map<number, NetworkUserNode[]>()
  const userMap = new Map<number, NetworkUserNode>()
  const hasParent = new Set<number>()

  for (const u of users) {
    userMap.set(u.id, u)
    if (u.referrer_id) {
      hasParent.add(u.id)
      const siblings = childrenMap.get(u.referrer_id) || []
      siblings.push(u)
      childrenMap.set(u.referrer_id, siblings)
    }
  }

  // Find roots: users who have referrals but no parent (top referrers)
  const roots = users
    .filter((u) => u.direct_referrals > 0 && !hasParent.has(u.id))
    .sort((a, b) => b.direct_referrals - a.direct_referrals)

  if (roots.length === 0) return { items: [], edges: [] }

  // BFS tree layout — each root gets its own tree, placed side by side
  const X_GAP = 100
  const Y_GAP = 110
  let globalOffsetX = 0

  for (const root of roots) {
    // Calculate subtree width first
    const subtreeWidth = calcSubtreeWidth(root.id, childrenMap, X_GAP)

    // BFS layout for this tree
    interface QItem { user: NetworkUserNode; depth: number; xCenter: number }
    const queue: QItem[] = [{ user: root, depth: 0, xCenter: globalOffsetX + subtreeWidth / 2 }]

    while (queue.length > 0) {
      const { user: u, depth, xCenter } = queue.shift()!
      const nodeId = `user-${u.id}`
      const r = getNodeRadius(u.direct_referrals)
      const color = getNodeColor(u)

      const x = xCenter
      const y = depth * Y_GAP

      items.push({
        id: nodeId, x, y, r, color,
        label: u.display_name || u.username || `#${u.id}`,
        count: u.direct_referrals,
        type: 'user',
        rawId: u.id,
      })
      posMap.set(nodeId, { x, y })

      // Layout children centered under this node
      const children = childrenMap.get(u.id) || []
      if (children.length > 0) {
        const childWidths = children.map((c) => calcSubtreeWidth(c.id, childrenMap, X_GAP))
        const totalChildWidth = childWidths.reduce((a, b) => a + b, 0)
        let childX = xCenter - totalChildWidth / 2

        children.forEach((child, i) => {
          const cw = childWidths[i]
          queue.push({ user: child, depth: depth + 1, xCenter: childX + cw / 2 })
          childX += cw
        })
      }
    }

    globalOffsetX += subtreeWidth + X_GAP * 2
  }

  // Build edges
  const edgeLines: { x1: number; y1: number; x2: number; y2: number; color: string; type: string }[] = []
  for (const e of (data.edges || [])) {
    const src = posMap.get(e.source)
    const tgt = posMap.get(e.target)
    if (!src || !tgt) continue
    edgeLines.push({
      x1: src.x, y1: src.y, x2: tgt.x, y2: tgt.y,
      color: e.type === 'campaign' ? '#f472b6' : '#4b5563',
      type: e.type,
    })
  }

  return { items, edges: edgeLines }
}

function calcSubtreeWidth(userId: number, childrenMap: Map<number, NetworkUserNode[]>, gap: number): number {
  const children = childrenMap.get(userId) || []
  if (children.length === 0) return gap
  const childWidths = children.map((c) => calcSubtreeWidth(c.id, childrenMap, gap))
  return childWidths.reduce((a, b) => a + b, 0)
}

// ── Legend ──

const LEGEND = [
  { color: '#10b981', label: 'paidActive' },
  { color: '#60a5fa', label: 'trial' },
  { color: '#f472b6', label: 'paidExpired' },
  { color: '#fb923c', label: 'trialExpired' },
  { color: '#fbbf24', label: 'partner' },
  { color: '#e879f9', label: 'topReferrer' },
  { color: '#818cf8', label: 'activeReferrer' },
  { color: '#6b7280', label: 'noSub' },
]

// ── Component ──

export default function BedolagaReferrals() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })
  const [hovered, setHovered] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedUser, setSelectedUser] = useState<NetworkUserNode | null>(null)

  const { data, isLoading, refetch } = useQuery<NetworkGraphData>({
    queryKey: ['bedolaga-referral-network'],
    queryFn: () => client.get('/bedolaga/referrals/network').then((r) => r.data),
    staleTime: 60_000,
  })

  const { items, edges } = useMemo(() => {
    if (!data) return { items: [], edges: [] }
    return layoutGraph(data)
  }, [data])

  // Filter by search
  const filteredItems = useMemo(() => {
    if (!search) return items
    const q = search.toLowerCase()
    const matchIds = new Set(items.filter((i) => i.label.toLowerCase().includes(q)).map((i) => i.id))
    return items.map((i) => ({ ...i, dimmed: !matchIds.has(i.id) }))
  }, [items, search])

  // SVG bounds
  const padding = 80
  const minX = items.length ? Math.min(...items.map((n) => n.x - n.r)) - padding : -200
  const maxX = items.length ? Math.max(...items.map((n) => n.x + n.r)) + padding : 200
  const minY = items.length ? Math.min(...items.map((n) => n.y - n.r)) - padding : -200
  const maxY = items.length ? Math.max(...items.map((n) => n.y + n.r)) + padding + 20 : 200
  const svgW = maxX - minX
  const svgH = maxY - minY

  // Pan/zoom handlers
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
    setZoom((z) => Math.max(0.2, Math.min(4, z - e.deltaY * 0.001)))
  }, [])

  const fitView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }) }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const prevent = (e: WheelEvent) => e.preventDefault()
    el.addEventListener('wheel', prevent, { passive: false })
    return () => el.removeEventListener('wheel', prevent)
  }, [])

  const handleNodeClick = (item: LayoutItem & { dimmed?: boolean }) => {
    if (item.type === 'user') {
      const u = data?.users.find((u) => u.id === item.rawId)
      if (u) setSelectedUser(u)
    }
  }

  const formatRubles = (kopeks?: number) => {
    if (!kopeks) return '0 ₽'
    return `${(kopeks / 100).toLocaleString('ru-RU')} ₽`
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="page-header">
          <Skeleton className="h-8 w-48" />
        </div>
        <Skeleton className="h-[500px] rounded-xl" />
      </div>
    )
  }

  return (
    <div className="space-y-4 md:space-y-6">
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="page-header-title">{t('bedolaga.referrals.title')}</h1>
          <p className="text-dark-200 mt-1 text-sm">{t('bedolaga.referrals.subtitle')}</p>
        </div>
        <div className="page-header-actions">
          <Button variant="secondary" size="icon" onClick={() => refetch()} disabled={isLoading}>
            <RefreshCw className={cn('w-5 h-5', isLoading && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card className="glass-card">
          <CardContent className="p-3 flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--glass-bg-hover)] text-blue-400"><Users className="w-4.5 h-4.5" /></div>
            <div><p className="text-xs text-dark-300">{t('bedolaga.referrals.totalUsers')}</p><p className="text-lg font-bold">{data?.total_users ?? 0}</p></div>
          </CardContent>
        </Card>
        <Card className="glass-card">
          <CardContent className="p-3 flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--glass-bg-hover)] text-violet-400"><Share2 className="w-4.5 h-4.5" /></div>
            <div><p className="text-xs text-dark-300">{t('bedolaga.referrals.totalReferrers')}</p><p className="text-lg font-bold">{data?.total_referrers ?? 0}</p></div>
          </CardContent>
        </Card>
        <Card className="glass-card">
          <CardContent className="p-3 flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--glass-bg-hover)] text-pink-400"><Megaphone className="w-4.5 h-4.5" /></div>
            <div><p className="text-xs text-dark-300">{t('bedolaga.referrals.totalCampaigns')}</p><p className="text-lg font-bold">{data?.total_campaigns ?? 0}</p></div>
          </CardContent>
        </Card>
        <Card className="glass-card">
          <CardContent className="p-3 flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--glass-bg-hover)] text-emerald-400"><Share2 className="w-4.5 h-4.5" /></div>
            <div><p className="text-xs text-dark-300">{t('bedolaga.referrals.totalEarnings')}</p><p className="text-lg font-bold text-emerald-400">{formatRubles(data?.total_earnings_kopeks)}</p></div>
          </CardContent>
        </Card>
      </div>

      {/* Graph */}
      <div className="relative rounded-xl overflow-hidden border border-[var(--glass-border)] bg-[var(--surface-body)]">
        {/* Search bar */}
        <div className="absolute top-3 left-3 right-3 z-10 flex gap-2">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dark-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('bedolaga.referrals.searchPlaceholder')}
              className="w-full h-8 pl-8 pr-8 rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg-solid)] text-xs placeholder:text-dark-400 focus:outline-none focus:ring-1 focus:ring-primary-500/50"
            />
            {search && (
              <button onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-dark-400 hover:text-white">
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* SVG Canvas */}
        <div
          ref={containerRef}
          className="h-[500px] sm:h-[600px] cursor-grab active:cursor-grabbing select-none overflow-hidden"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
        >
          {items.length === 0 ? (
            <div className="h-full flex items-center justify-center text-dark-400">
              <div className="text-center">
                <Share2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>{t('bedolaga.referrals.noData')}</p>
              </div>
            </div>
          ) : (
            <svg
              width="100%" height="100%"
              viewBox={`${minX} ${minY} ${svgW} ${svgH}`}
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: 'center center' }}
            >
              {/* Edges */}
              {edges.map((e, i) => (
                <line
                  key={i}
                  x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                  stroke={e.color}
                  strokeWidth={e.type === 'campaign' ? 1.5 : 1}
                  strokeOpacity={0.3}
                />
              ))}
              {/* Nodes */}
              {(filteredItems as (LayoutItem & { dimmed?: boolean })[]).map((item) => {
                const isHovered = hovered === item.id
                const dimmed = (item as any).dimmed
                const scale = isHovered ? 1.15 : 1
                const isCampaign = item.type === 'campaign'
                return (
                  <g
                    key={item.id}
                    data-node
                    className="cursor-pointer"
                    transform={`translate(${item.x}, ${item.y}) scale(${scale})`}
                    style={{ transition: 'transform 0.15s ease', opacity: dimmed ? 0.15 : 1 }}
                    onClick={() => handleNodeClick(item)}
                    onMouseEnter={() => setHovered(item.id)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    {/* Glow */}
                    <circle r={item.r + 5} fill={item.color.glow} opacity={isHovered ? 0.7 : 0.35} />
                    {/* Shape */}
                    {isCampaign ? (
                      <rect
                        x={-item.r} y={-item.r} width={item.r * 2} height={item.r * 2}
                        rx={6} fill={item.color.fill} stroke={item.color.border} strokeWidth={2} opacity={0.9}
                      />
                    ) : (
                      <circle r={item.r} fill={item.color.fill} stroke={item.color.border} strokeWidth={2} opacity={0.9} />
                    )}
                    {/* Initial */}
                    <text
                      textAnchor="middle" dominantBaseline="central"
                      fill="white" fontWeight="700"
                      fontSize={Math.max(8, item.r * 0.65)}
                      style={{ pointerEvents: 'none' }}
                    >
                      {isCampaign ? '🎯' : item.label.charAt(0).toUpperCase()}
                    </text>
                    {/* Label */}
                    {item.r >= 14 && (
                      <text
                        y={item.r + 13} textAnchor="middle"
                        fill="#d1d5db" fontSize={9} fontWeight="500"
                        style={{ pointerEvents: 'none' }}
                      >
                        {item.label.length > 12 ? item.label.slice(0, 11) + '…' : item.label}
                        {item.count > 0 ? ` | ${item.count}` : ''}
                      </text>
                    )}
                  </g>
                )
              })}
            </svg>
          )}
        </div>

        {/* Zoom controls */}
        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1 p-1 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)]">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setZoom((z) => Math.min(4, z + 0.25))}>
            <ZoomIn className="w-3.5 h-3.5 text-dark-200" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setZoom((z) => Math.max(0.2, z - 0.25))}>
            <ZoomOut className="w-3.5 h-3.5 text-dark-200" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fitView}>
            <Maximize2 className="w-3.5 h-3.5 text-dark-200" />
          </Button>
        </div>

        {/* Legend */}
        <div className="absolute bottom-3 right-3 p-2.5 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)]">
          <p className="text-[10px] text-dark-400 uppercase tracking-wider mb-1.5">{t('bedolaga.customerDetail.refLegendTitle')}</p>
          <div className="space-y-1">
            {LEGEND.map(({ color, label }) => (
              <div key={label} className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}66` }} />
                <span className="text-[10px] text-dark-200">{t(`bedolaga.customerDetail.refLegend.${label}`)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* User detail panel */}
      {selectedUser && (
        <Card className="glass-card animate-fade-in">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-medium text-sm flex items-center gap-2">
                <Users className="w-4 h-4 text-blue-400" />
                {selectedUser.display_name || selectedUser.username || `#${selectedUser.id}`}
              </h3>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" className="text-xs" onClick={() => navigate(`/bedolaga/customers/${selectedUser.id}`)}>
                  {t('bedolaga.referrals.openProfile')}
                </Button>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setSelectedUser(null)}>
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div>
                <p className="text-dark-400">{t('bedolaga.referrals.directReferrals')}</p>
                <p className="font-bold text-lg">{selectedUser.direct_referrals}</p>
              </div>
              <div>
                <p className="text-dark-400">{t('bedolaga.referrals.branchUsers')}</p>
                <p className="font-bold text-lg">{selectedUser.total_branch_users ?? 0}</p>
              </div>
              <div>
                <p className="text-dark-400">{t('bedolaga.referrals.branchRevenue')}</p>
                <p className="font-bold text-lg text-emerald-400">{formatRubles(selectedUser.branch_revenue_kopeks)}</p>
              </div>
              <div>
                <p className="text-dark-400">{t('bedolaga.referrals.personalRevenue')}</p>
                <p className="font-bold text-lg">{formatRubles(selectedUser.personal_revenue_kopeks)}</p>
              </div>
            </div>
            {selectedUser.subscription_name && (
              <div className="mt-2 pt-2 border-t border-[var(--glass-border)]">
                <Badge className="text-[10px] bg-[var(--glass-bg-hover)]">
                  {selectedUser.subscription_name}
                  {selectedUser.subscription_end && ` → ${new Date(selectedUser.subscription_end).toLocaleDateString('ru-RU')}`}
                </Badge>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
