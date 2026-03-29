import client from './client'

export const NODE_NETWORK_POLICY_CONNECTION_TYPES = [
  'mobile',
  'mobile_isp',
  'fixed',
  'isp',
  'regional_isp',
  'residential',
  'hosting',
  'vpn',
  'business',
] as const

export type NodeNetworkPolicyConnectionType =
  (typeof NODE_NETWORK_POLICY_CONNECTION_TYPES)[number]

export interface NodeNetworkPolicy {
  id: number
  node_uuid: string
  is_enabled: boolean
  expected_connection_types: NodeNetworkPolicyConnectionType[]
  strict_mode: boolean
  violation_score: number
  reason_template: string | null
  created_at: string | null
  updated_at: string | null
}

export interface NodeNetworkPolicyPayload {
  is_enabled: boolean
  expected_connection_types: NodeNetworkPolicyConnectionType[]
  strict_mode: boolean
  violation_score: number
  reason_template?: string | null
}

export async function listNodePolicies(): Promise<NodeNetworkPolicy[]> {
  const { data } = await client.get('/node-policies')
  if (Array.isArray(data)) return data
  return data?.items || []
}

export async function getNodePolicy(nodeUuid: string): Promise<NodeNetworkPolicy | null> {
  const { data } = await client.get(`/node-policies/${nodeUuid}`)
  return data || null
}

export async function upsertNodePolicy(
  nodeUuid: string,
  payload: NodeNetworkPolicyPayload,
): Promise<NodeNetworkPolicy> {
  const { data } = await client.put(`/node-policies/${nodeUuid}`, payload)
  return data
}

export async function deleteNodePolicy(nodeUuid: string): Promise<void> {
  await client.delete(`/node-policies/${nodeUuid}`)
}
