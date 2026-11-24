/** 限速准入数据层：与后端 /api/rate-admission 一一对应，页面只做展示和动作。 */

export type Policy = {
  id: number
  站点: string
  版本: number
  窗口阈值: number
  窗口秒数: number
  状态: string
  更新时间: string
  备注: string
}

export type WindowRow = {
  id: number
  站点: string
  窗口: string
  派发页数: number
  预留页数: number
  延期页数: number
  策略版本: number
  窗口阈值: number
  更新时间: string
}

export type Claim = {
  id: number
  领取标识: string
  幂等键: string
  站点: string
  窗口: string
  申请页数: number
  获准页数: number
  暂缓页数: number
  派发页数: number
  待发页数: number
  已发范围: string
  待发范围: string
  延期窗口: string
  预核策略版本: number
  预核窗口阈值: number
  "策略版本(采用)": number
  采用窗口阈值: number
  处理方式: string
  冲突: boolean
  状态: string
  创建时间: string
  更新时间: string
}

export type WindowState = {
  站点: string
  窗口: string
  派发页数: number
  预留页数: number
  延期页数: number
  本次已发范围: string
  本次待发范围: string
  预核窗口阈值: number
  预核策略版本: number
  按预核阈值剩余: number
  当前窗口阈值: number
  当前策略版本: number
  按当前阈值剩余: number
}

export type AdmissionLog = {
  id: number
  日志标识: string
  站点: string
  窗口: string
  领取标识: string
  幂等键: string
  事件: string
  判定: string
  策略版本: number
  预核策略版本: number
  窗口阈值: number
  申请页数: number
  获准页数: number
  派发页数: number
  延期页数: number
  页码范围: string
  领取已发范围: string
  领取待发范围: string
  判定依据: string
  备注: string
  记录时间: string
}

export type AdmissionState = {
  policies: Policy[]
  windows: WindowRow[]
  claims: Claim[]
  logs: AdmissionLog[]
}

export type AdmissionResult = {
  ok: boolean
  message: string
  repeated?: boolean
  conflict?: boolean
  claim?: Claim
  window_state?: WindowState
  current_policy?: Policy
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function loadAdmissionState(site?: string): Promise<AdmissionState> {
  const query = site ? `?site=${encodeURIComponent(site)}` : ''
  return call<AdmissionState>(`/api/rate-admission/state${query}`)
}

export function savePolicy(payload: {
  site: string
  limit_per_window: number
  window_seconds?: number
  note?: string
}): Promise<{ ok: boolean; message: string }> {
  return call('/api/rate-admission/policies', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createClaim(payload: {
  site: string
  requested_pages: number
  window?: string
  idempotency_key?: string
}): Promise<AdmissionResult> {
  return call('/api/rate-admission/claims', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function confirmClaim(
  claimId: string,
  payload: {
    resolution?: 'honor_original' | 'apply_current'
    idempotency_key?: string
    dispatch_pages?: number
  },
): Promise<AdmissionResult> {
  return call(`/api/rate-admission/claims/${encodeURIComponent(claimId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function dispatchClaim(
  claimId: string,
  payload: { pages?: number; idempotency_key?: string },
): Promise<AdmissionResult> {
  return call(`/api/rate-admission/claims/${encodeURIComponent(claimId)}/dispatch`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
