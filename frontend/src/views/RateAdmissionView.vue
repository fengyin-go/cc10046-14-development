<template>
  <section class="page" data-testid="rate-admission">
    <header class="page-head">
      <div>
        <h2>限速准入</h2>
        <p class="page-desc">
          按来源、实际窗口和策略版本核算可派发页数；已发与暂缓分开记账，重复领取不会再次扣减窗口。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>来源站点</span>
          <select v-model="site" data-testid="site-filter" @change="reload">
            <option value="">全部站点</option>
            <option v-for="item in siteOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <button class="btn" data-testid="refresh" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="message" class="notice" data-testid="message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <section class="panel">
      <h3>窗口策略</h3>
      <form class="analysis-form" @submit.prevent="submitPolicy">
        <label>
          <span>站点</span>
          <input v-model="policyForm.site" data-testid="policy-site" required />
        </label>
        <label>
          <span>每窗口页数</span>
          <input v-model.number="policyForm.limit" data-testid="policy-limit" min="0" type="number" required />
        </label>
        <label>
          <span>窗口秒数</span>
          <input v-model.number="policyForm.seconds" data-testid="policy-seconds" min="1" type="number" />
        </label>
        <button class="btn primary" data-testid="save-policy" type="submit">保存新版本</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>站点</th><th>版本</th><th>窗口阈值</th><th>窗口秒数</th><th>状态</th><th>更新时间</th><th>备注</th></tr>
        </thead>
        <tbody>
          <tr v-for="policy in state.policies" :key="policy.id" :data-testid="`policy-${policy.站点}-${policy.版本}`">
            <td>{{ policy.站点 }}</td>
            <td>v{{ policy.版本 }}</td>
            <td>{{ policy.窗口阈值 }} 页</td>
            <td>{{ policy.窗口秒数 }}s</td>
            <td>{{ policy.状态 }}</td>
            <td>{{ policy.更新时间 }}</td>
            <td>{{ policy.备注 }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3>领取预核</h3>
      <form class="analysis-form" @submit.prevent="submitClaim">
        <label>
          <span>站点</span>
          <input v-model="claimForm.site" data-testid="claim-site" required />
        </label>
        <label>
          <span>申请页数</span>
          <input v-model.number="claimForm.pages" data-testid="claim-pages" min="1" type="number" required />
        </label>
        <label>
          <span>领取幂等键</span>
          <input v-model="claimForm.key" data-testid="claim-key" placeholder="留空自动生成" />
        </label>
        <button class="btn primary" data-testid="create-claim" type="submit">预核领取</button>
      </form>
      <p class="hint">预核只占用窗口额度，尚未实际取页；同站不同站点各用各的额度。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>领取</th><th>站点</th><th>窗口</th><th>申请</th><th>获准</th><th>已派发</th><th>暂缓</th>
            <th>已发范围</th><th>待发范围</th><th>预核版本</th><th>采用版本</th><th>状态</th><th>动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="claim in state.claims" :key="claim.id" :data-testid="`claim-${claim.领取标识}`">
            <td>{{ claim.领取标识 }}</td>
            <td>{{ claim.站点 }}</td>
            <td>{{ claim.窗口 }}</td>
            <td>{{ claim.申请页数 }}</td>
            <td>{{ claim.获准页数 }}</td>
            <td>{{ claim.派发页数 }}</td>
            <td>{{ claim.暂缓页数 }}</td>
            <td>{{ claim.已发范围 || '—' }}</td>
            <td>{{ claim.待发范围 || '—' }}</td>
            <td>v{{ claim.预核策略版本 }}（{{ claim.预核窗口阈值 }} 页）</td>
            <td>v{{ claim['策略版本(采用)'] }}（{{ claim.采用窗口阈值 }} 页）</td>
            <td>
              {{ claim.状态 }}
              <span v-if="claim.冲突" class="tag-warn">冲突</span>
            </td>
            <td class="row-actions">
              <button
                v-if="claim.状态 === '已预核'"
                class="link"
                type="button"
                :data-testid="`confirm-${claim.领取标识}`"
                @click="confirm(claim, 'honor_original')"
              >沿用原额度</button>
              <button
                v-if="claim.状态 === '已预核'"
                class="link"
                type="button"
                :data-testid="`confirm-current-${claim.领取标识}`"
                @click="confirm(claim, 'apply_current')"
              >按新阈值</button>
              <button
                v-if="claim.待发页数 > 0 && claim.状态 !== '已预核'"
                class="link"
                type="button"
                :data-testid="`dispatch-${claim.领取标识}`"
                @click="dispatch(claim)"
              >续做未发</button>
            </td>
          </tr>
          <tr v-if="!state.claims.length">
            <td colspan="13" class="empty-state">暂无领取记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3>窗口账本</h3>
      <table class="data-table">
        <thead>
          <tr><th>站点</th><th>窗口</th><th>已派发</th><th>预留</th><th>延期</th><th>账本版本</th><th>账本阈值</th><th>更新时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in state.windows" :key="row.id" :data-testid="`window-${row.站点}-${row.窗口}`">
            <td>{{ row.站点 }}</td>
            <td>{{ row.窗口 }}</td>
            <td>{{ row.派发页数 }}</td>
            <td>{{ row.预留页数 }}</td>
            <td>{{ row.延期页数 }}</td>
            <td>v{{ row.策略版本 }}</td>
            <td>{{ row.窗口阈值 }} 页</td>
            <td>{{ row.更新时间 }}</td>
          </tr>
          <tr v-if="!state.windows.length">
            <td colspan="8" class="empty-state">暂无窗口账本</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3>限速准入日志</h3>
      <p class="hint">每条日志都带本次判定采用的策略版本；实际派发单独记一条，可与未获准范围逐页核对。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>日志</th><th>站点</th><th>窗口</th><th>领取</th><th>事件</th><th>判定</th>
            <th>策略版本</th><th>阈值</th><th>页码</th><th>派发</th><th>延期</th><th>依据</th><th>备注</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in state.logs" :key="log.id" :data-testid="`log-${log.日志标识}`" :data-event="log.事件">
            <td>{{ log.日志标识 }}</td>
            <td>{{ log.站点 }}</td>
            <td>{{ log.窗口 }}</td>
            <td>{{ log.领取标识 }}</td>
            <td>{{ log.事件 }}</td>
            <td>{{ log.判定 }}</td>
            <td>v{{ log.策略版本 }}</td>
            <td>{{ log.窗口阈值 }} 页</td>
            <td>{{ log.页码范围 || '—' }}</td>
            <td>{{ log.派发页数 }}</td>
            <td>{{ log.延期页数 }}</td>
            <td>{{ log.判定依据 }}</td>
            <td>{{ log.备注 }}</td>
            <td>{{ log.记录时间 }}</td>
          </tr>
          <tr v-if="!state.logs.length">
            <td colspan="14" class="empty-state">暂无准入日志</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  confirmClaim,
  createClaim,
  dispatchClaim,
  loadAdmissionState,
  savePolicy,
} from '@/api/rate-admission'
import type { AdmissionState, Claim } from '@/api/rate-admission'

const site = ref('')
const message = ref('')
const errorMessage = ref('')
const state = ref<AdmissionState>({ policies: [], windows: [], claims: [], logs: [] })
const policyForm = reactive({ site: '甲站', limit: 6, seconds: 60 })
const claimForm = reactive({ site: '甲站', pages: 4, key: '' })

const siteOptions = computed(() =>
  Array.from(new Set(state.value.policies.map((row) => row.站点))),
)

function resetNotice() {
  message.value = ''
  errorMessage.value = ''
}

async function reload() {
  errorMessage.value = ''
  try {
    state.value = await loadAdmissionState(site.value || undefined)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '限速准入数据读取失败'
  }
}

async function submitPolicy() {
  resetNotice()
  try {
    const result = await savePolicy({
      site: policyForm.site,
      limit_per_window: policyForm.limit,
      window_seconds: policyForm.seconds,
    })
    message.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '策略保存失败'
  }
}

async function submitClaim() {
  resetNotice()
  try {
    const result = await createClaim({
      site: claimForm.site,
      requested_pages: claimForm.pages,
      idempotency_key: claimForm.key || undefined,
    })
    message.value = result.message
    if (result.claim) claimForm.key = result.claim.幂等键
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '预核失败'
  }
}

async function confirm(claim: Claim, resolution: 'honor_original' | 'apply_current') {
  resetNotice()
  try {
    const result = await confirmClaim(claim.领取标识, {
      resolution,
      idempotency_key: `ACK-${claim.领取标识}`,
    })
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认失败'
  }
}

async function dispatch(claim: Claim) {
  resetNotice()
  try {
    const result = await dispatchClaim(claim.领取标识, {
      pages: claim.待发页数,
      idempotency_key: `DSP-${claim.领取标识}-${claim.派发页数}`,
    })
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '派发失败'
  }
}

onMounted(reload)
</script>
