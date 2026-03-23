import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ReactFlow,
  ReactFlowProvider,
  Node,
  Edge,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
// utils

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
  created_at?: string
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

// ── Subscription-based colors (matching Bedolaga style) ──

type SubType = 'paid_active' | 'trial' | 'paid_expired' | 'trial_expired' | 'partner' | 'top_referrer' | 'active_referrer' | 'no_sub'

function getSubType(node: { status?: string; subscription_status?: string; is_trial?: boolean; referral_count?: number }): SubType {
  const rc = node.referral_count ?? 0
  if (rc >= 10) return 'top_referrer'
  if (rc >= 3) return 'active_referrer'

  const sub = node.subscription_status
  const trial = node.is_trial

  if (!sub || sub === 'none') return 'no_sub'
  if (sub === 'active' && trial) return 'trial'
  if (sub === 'active') return 'paid_active'
  if (sub === 'expired' && trial) return 'trial_expired'
  if (sub === 'expired') return 'paid_expired'
  return 'no_sub'
}

const SUB_COLORS: Record<SubType, { fill: string; glow: string; border: string }> = {
  paid_active:    { fill: '#10b981', glow: 'rgba(16,185,129,0.35)',  border: '#34d399' },
  trial:          { fill: '#60a5fa', glow: 'rgba(96,165,250,0.35)',  border: '#93c5fd' },
  paid_expired:   { fill: '#f472b6', glow: 'rgba(244,114,182,0.3)', border: '#f9a8d4' },
  trial_expired:  { fill: '#fb923c', glow: 'rgba(251,146,60,0.35)', border: '#fdba74' },
  partner:        { fill: '#fbbf24', glow: 'rgba(251,191,36,0.3)',  border: '#fcd34d' },
  top_referrer:   { fill: '#e879f9', glow: 'rgba(232,121,249,0.4)', border: '#f0abfc' },
  active_referrer:{ fill: '#818cf8', glow: 'rgba(129,140,248,0.35)',border: '#a5b4fc' },
  no_sub:         { fill: '#6b7280', glow: 'rgba(107,114,128,0.2)', border: '#9ca3af' },
}

const LEGEND: { type: SubType; label: string }[] = [
  { type: 'paid_active',     label: 'refLegend.paidActive' },
  { type: 'trial',           label: 'refLegend.trial' },
  { type: 'paid_expired',    label: 'refLegend.paidExpired' },
  { type: 'trial_expired',   label: 'refLegend.trialExpired' },
  { type: 'top_referrer',    label: 'refLegend.topReferrer' },
  { type: 'active_referrer', label: 'refLegend.activeReferrer' },
  { type: 'no_sub',          label: 'refLegend.noSub' },
]

// ── Circle Node ──

function CircleNode({ data }: NodeProps) {
  const d = data as any
  const navigate = useNavigate()
  const colors = SUB_COLORS[d.subType as SubType] || SUB_COLORS.no_sub
  const size: number = d.size || 40
  const showLabel = size >= 36

  return (
    <div
      className="relative cursor-pointer transition-transform hover:scale-110"
      style={{ width: size, height: size }}
      onClick={() => navigate(`/bedolaga/customers/${d.userId}`)}
    >
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0 !w-0 !h-0" />

      {/* Glow */}
      <div
        className="absolute inset-0 rounded-full animate-pulse"
        style={{
          background: colors.glow,
          filter: `blur(${size * 0.3}px)`,
          opacity: d.isRoot ? 0.6 : 0.4,
        }}
      />

      {/* Circle */}
      <div
        className="absolute inset-0 rounded-full border-2 flex items-center justify-center"
        style={{
          borderColor: colors.border,
          background: `radial-gradient(circle at 35% 35%, ${colors.fill}dd, ${colors.fill}88)`,
        }}
      >
        {/* Inner initial */}
        <span
          className="text-white font-bold select-none"
          style={{ fontSize: Math.max(10, size * 0.32) }}
        >
          {(d.username || d.firstName || '#').charAt(0).toUpperCase()}
        </span>
      </div>

      {/* Label below */}
      {showLabel && (
        <div
          className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap text-center pointer-events-none"
          style={{ top: size + 4 }}
        >
          <span className="text-[10px] font-medium text-dark-100 drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)]">
            {d.username || d.firstName || `#${d.userId}`}
            {(d.referralCount ?? 0) > 0 && (
              <span className="text-dark-300 ml-0.5">| {d.referralCount}</span>
            )}
          </span>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0 !w-0 !h-0" />
    </div>
  )
}

const nodeTypes = { circleNode: CircleNode }

// ── Build Graph ──

function getNodeSize(referralCount: number, isRoot: boolean): number {
  if (isRoot) return Math.min(80, 50 + referralCount * 2)
  if (referralCount >= 10) return 56
  if (referralCount >= 5) return 46
  if (referralCount >= 1) return 38
  return 28
}

function buildGraph(
  rootUser: ReferralGraphProps['rootUser'],
  tree: ReferralNode[],
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = []
  const edges: Edge[] = []

  const X_GAP = 160
  const Y_GAP = 120

  const rootRc = rootUser.referral_count ?? tree.length
  const rootSize = getNodeSize(rootRc, true)
  const rootSubType = getSubType(rootUser)

  nodes.push({
    id: `u-${rootUser.id}`,
    type: 'circleNode',
    position: { x: 0, y: 0 },
    data: {
      userId: rootUser.id,
      username: rootUser.username,
      firstName: rootUser.first_name,
      subType: rootSubType,
      size: rootSize,
      isRoot: true,
      referralCount: rootRc,
    },
  })

  interface QItem { node: ReferralNode; parentId: string; depth: number }
  const queue: QItem[] = tree.map((c) => ({ node: c, parentId: `u-${rootUser.id}`, depth: 1 }))

  const depthNodes: Record<number, string[]> = {}

  while (queue.length > 0) {
    const { node, parentId, depth } = queue.shift()!
    const nodeId = `u-${node.id}`
    const rc = node.referral_count ?? (node.children?.length ?? 0)
    const size = getNodeSize(rc, false)
    const subType = getSubType(node)

    if (!depthNodes[depth]) depthNodes[depth] = []
    depthNodes[depth].push(nodeId)

    nodes.push({
      id: nodeId,
      type: 'circleNode',
      position: { x: 0, y: depth * Y_GAP },
      data: {
        userId: node.id,
        username: node.username,
        firstName: node.first_name,
        subType,
        size,
        isRoot: false,
        referralCount: rc,
      },
    })

    edges.push({
      id: `e-${parentId}-${nodeId}`,
      source: parentId,
      target: nodeId,
      type: 'smoothstep',
      animated: true,
      style: {
        stroke: SUB_COLORS[subType]?.border || '#6b7280',
        strokeWidth: 1.5,
        opacity: 0.5,
      },
    })

    if (node.children) {
      node.children.forEach((child) => {
        queue.push({ node: child, parentId: nodeId, depth: depth + 1 })
      })
    }
  }

  // Center nodes at each depth
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))
  for (const [, ids] of Object.entries(depthNodes)) {
    const totalWidth = (ids.length - 1) * X_GAP
    const startX = -totalWidth / 2
    ids.forEach((id, i) => {
      const n = nodeMap.get(id)
      if (n) n.position.x = startX + i * X_GAP
    })
  }

  return { nodes, edges }
}

// ── Main Component ──

export default function ReferralGraph(props: ReferralGraphProps) {
  return (
    <ReactFlowProvider>
      <ReferralGraphInner {...props} />
    </ReactFlowProvider>
  )
}

function ReferralGraphInner({ rootUser, tree, stats }: ReferralGraphProps) {
  const { t } = useTranslation()

  const { nodes: initialNodes, edges: initialEdges } = useMemo(
    () => buildGraph(rootUser, tree),
    [rootUser, tree],
  )

  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)

  const totalNodes = nodes.length
  const totalReferrers = nodes.filter((n) => ((n.data as any).referralCount ?? 0) > 0).length

  return (
    <div className="relative h-[450px] sm:h-[550px] rounded-xl overflow-hidden border border-[var(--glass-border)] bg-[var(--surface-body)]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.4 }}
        minZoom={0.2}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background color="rgba(99,102,241,0.04)" gap={24} size={1} />
        <Controls
          showInteractive={false}
          className="!bg-[var(--glass-bg-solid)] !border-[var(--glass-border)] !shadow-lg [&>button]:!bg-transparent [&>button]:!border-[var(--glass-border)] [&>button]:!text-dark-200 [&>button:hover]:!bg-[var(--glass-bg-hover)]"
        />
      </ReactFlow>

      {/* Stats overlay — bottom left */}
      <div className="absolute bottom-3 left-3 p-3 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)] backdrop-blur-sm text-xs space-y-1.5 min-w-[140px]">
        <div className="flex justify-between gap-4">
          <span className="text-dark-300 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refGraphUsers')}</span>
          <span className="font-bold">{stats?.total_users ?? totalNodes}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-dark-300 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refGraphReferrers')}</span>
          <span className="font-bold">{stats?.total_referrers ?? totalReferrers}</span>
        </div>
        {(stats?.total_earnings_rubles ?? 0) > 0 && (
          <div className="flex justify-between gap-4">
            <span className="text-dark-300 uppercase text-[10px] tracking-wider">{t('bedolaga.customerDetail.refEarnings')}</span>
            <span className="font-bold text-emerald-400">{stats!.total_earnings_rubles!.toLocaleString()} ₽</span>
          </div>
        )}
      </div>

      {/* Legend — bottom right */}
      <div className="absolute bottom-3 right-3 p-3 rounded-lg bg-[var(--glass-bg-solid)] border border-[var(--glass-border)] backdrop-blur-sm">
        <p className="text-[10px] text-dark-300 uppercase tracking-wider mb-1.5">{t('bedolaga.customerDetail.refLegendTitle')}</p>
        <div className="space-y-1">
          {LEGEND.map(({ type, label }) => (
            <div key={type} className="flex items-center gap-2">
              <span
                className="w-3 h-3 rounded-full flex-shrink-0 border"
                style={{ backgroundColor: SUB_COLORS[type].fill, borderColor: SUB_COLORS[type].border }}
              />
              <span className="text-[10px] text-dark-200">{t(`bedolaga.customerDetail.${label}`)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
