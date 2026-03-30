import client from './client'
import {
  NODE_NETWORK_POLICY_CONNECTION_TYPES,
  deleteNodePolicy,
  listNodePolicies,
  upsertNodePolicy,
  type NodeNetworkPolicy,
  type NodeNetworkPolicyConnectionType,
  type NodeNetworkPolicyPayload,
} from './nodes'

export type {
  NodeNetworkPolicy,
  NodeNetworkPolicyConnectionType,
  NodeNetworkPolicyPayload,
}

export { NODE_NETWORK_POLICY_CONNECTION_TYPES }

export interface BanhammerSettings {
  enabled: boolean
  warning_limit: number
  warning_cooldown_sec: number
  block_stages_minutes: number[]
  warning_template: string | null
}

export type BanhammerSettingsPayload = BanhammerSettings

export interface BanhammerEventRecord extends Record<string, unknown> {
  id?: number | string
  created_at?: string | null
}

export interface BanhammerStateRecord extends Record<string, unknown> {
  id?: number | string
  user_uuid?: string | null
}

export interface BanhammerNodeSummary {
  uuid: string
  name: string
  address: string
  port: number
  is_connected: boolean
  is_disabled: boolean
}

export interface BanhammerBedolagaStatus {
  configured: boolean
  reachable: boolean
  health_ok: boolean
  auth_ok: boolean
  ban_notifications_endpoint_ok: boolean
  health_status_code: number | null
  probe_status_code: number | null
  detail: string | null
  checked_at: string | null
}

interface BanhammerListResult<T> {
  items: T[]
  total: number
}

const DEFAULT_BANHAMMER_SETTINGS: BanhammerSettings = {
  enabled: false,
  warning_limit: 3,
  warning_cooldown_sec: 60,
  block_stages_minutes: [15, 60, 360, 720, 1440],
  warning_template: null,
}

const DEFAULT_BANHAMMER_BEDOLAGA_STATUS: BanhammerBedolagaStatus = {
  configured: false,
  reachable: false,
  health_ok: false,
  auth_ok: false,
  ban_notifications_endpoint_ok: false,
  health_status_code: null,
  probe_status_code: null,
  detail: null,
  checked_at: null,
}

function toNumber(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function toBoolean(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true' || normalized === '1') return true
    if (normalized === 'false' || normalized === '0') return false
  }
  return fallback
}

function toNullableString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function toNumberArray(value: unknown, fallback: number[]): number[] {
  if (Array.isArray(value)) {
    const parsed = value
      .map((item) => toNumber(item, Number.NaN))
      .filter((item) => Number.isFinite(item) && item > 0)
    return parsed.length > 0 ? parsed : fallback
  }
  if (typeof value === 'string') {
    const parsed = value
      .split(',')
      .map((item) => toNumber(item.trim(), Number.NaN))
      .filter((item) => Number.isFinite(item) && item > 0)
    return parsed.length > 0 ? parsed : fallback
  }
  return fallback
}

function normalizeBanhammerSettings(payload: unknown): BanhammerSettings {
  const src = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  return {
    enabled: toBoolean(
      src.banhammer_enabled ?? src.enabled ?? src.is_enabled,
      DEFAULT_BANHAMMER_SETTINGS.enabled,
    ),
    warning_limit: toNumber(
      src.banhammer_warning_limit ?? src.warning_limit,
      DEFAULT_BANHAMMER_SETTINGS.warning_limit,
    ),
    warning_cooldown_sec: toNumber(
      src.banhammer_warning_cooldown_seconds ?? src.warning_cooldown_sec ?? src.warning_cooldown_seconds,
      DEFAULT_BANHAMMER_SETTINGS.warning_cooldown_sec,
    ),
    block_stages_minutes: toNumberArray(
      src.banhammer_block_stages_minutes ?? src.block_stages_minutes ?? src.block_stage_minutes,
      DEFAULT_BANHAMMER_SETTINGS.block_stages_minutes,
    ),
    warning_template: toNullableString(
      src.banhammer_warning_template ?? src.warning_template,
    ),
  }
}

function normalizeListResult<T extends Record<string, unknown>>(payload: unknown): BanhammerListResult<T> {
  if (Array.isArray(payload)) {
    return { items: payload as T[], total: payload.length }
  }

  if (payload && typeof payload === 'object') {
    const src = payload as Record<string, unknown>
    const itemsCandidate = src.items ?? src.results ?? src.data
    const items = Array.isArray(itemsCandidate) ? (itemsCandidate as T[]) : []
    const total = toNumber(src.total ?? src.count, items.length)
    return { items, total }
  }

  return { items: [], total: 0 }
}

function normalizeBanhammerBedolagaStatus(payload: unknown): BanhammerBedolagaStatus {
  const src = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  return {
    configured: toBoolean(src.configured, DEFAULT_BANHAMMER_BEDOLAGA_STATUS.configured),
    reachable: toBoolean(src.reachable, DEFAULT_BANHAMMER_BEDOLAGA_STATUS.reachable),
    health_ok: toBoolean(src.health_ok, DEFAULT_BANHAMMER_BEDOLAGA_STATUS.health_ok),
    auth_ok: toBoolean(src.auth_ok, DEFAULT_BANHAMMER_BEDOLAGA_STATUS.auth_ok),
    ban_notifications_endpoint_ok: toBoolean(
      src.ban_notifications_endpoint_ok,
      DEFAULT_BANHAMMER_BEDOLAGA_STATUS.ban_notifications_endpoint_ok,
    ),
    health_status_code: Number.isFinite(toNumber(src.health_status_code, Number.NaN))
      ? toNumber(src.health_status_code, Number.NaN)
      : null,
    probe_status_code: Number.isFinite(toNumber(src.probe_status_code, Number.NaN))
      ? toNumber(src.probe_status_code, Number.NaN)
      : null,
    detail: toNullableString(src.detail),
    checked_at: toNullableString(src.checked_at),
  }
}

export async function getBanhammerSettings(): Promise<BanhammerSettings> {
  const { data } = await client.get('/violations/banhammer/settings')
  return normalizeBanhammerSettings(data)
}

export async function updateBanhammerSettings(
  payload: BanhammerSettingsPayload,
): Promise<BanhammerSettings> {
  const body = {
    banhammer_enabled: payload.enabled,
    banhammer_warning_limit: payload.warning_limit,
    banhammer_warning_cooldown_seconds: payload.warning_cooldown_sec,
    banhammer_block_stages_minutes: payload.block_stages_minutes,
    banhammer_warning_template: payload.warning_template ?? '',
  }
  const { data } = await client.put('/violations/banhammer/settings', body)
  return normalizeBanhammerSettings(data)
}

function toPageParams(limit?: number, offset?: number): { page: number; per_page: number } {
  const perPage = limit && limit > 0 ? Math.floor(limit) : 20
  const safeOffset = offset && offset > 0 ? Math.floor(offset) : 0
  const page = Math.floor(safeOffset / perPage) + 1
  return { page, per_page: perPage }
}

export async function listBanhammerEvents(params?: {
  limit?: number
  offset?: number
  user_uuid?: string
}): Promise<BanhammerListResult<BanhammerEventRecord>> {
  const paging = toPageParams(params?.limit, params?.offset)
  const { data } = await client.get('/violations/banhammer/events', {
    params: {
      page: paging.page,
      per_page: paging.per_page,
      user_uuid: params?.user_uuid,
    },
  })
  return normalizeListResult<BanhammerEventRecord>(data)
}

export async function listBanhammerStates(params?: {
  limit?: number
  offset?: number
  only_blocked?: boolean
}): Promise<BanhammerListResult<BanhammerStateRecord>> {
  const paging = toPageParams(params?.limit, params?.offset)
  const { data } = await client.get('/violations/banhammer/states', {
    params: {
      page: paging.page,
      per_page: paging.per_page,
      only_blocked: params?.only_blocked,
    },
  })
  return normalizeListResult<BanhammerStateRecord>(data)
}

export async function listBanhammerNodes(): Promise<BanhammerNodeSummary[]> {
  const { data } = await client.get('/nodes', { params: { per_page: 500 } })
  if (Array.isArray(data)) return data as BanhammerNodeSummary[]
  if (Array.isArray(data?.items)) return data.items as BanhammerNodeSummary[]
  return []
}

export async function getBanhammerBedolagaStatus(): Promise<BanhammerBedolagaStatus> {
  const { data } = await client.get('/violations/banhammer/bedolaga-status')
  return normalizeBanhammerBedolagaStatus(data)
}

export const listBanhammerNodePolicies = listNodePolicies
export const upsertBanhammerNodePolicy = upsertNodePolicy
export const deleteBanhammerNodePolicy = deleteNodePolicy
