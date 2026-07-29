<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const health = ref(null);
const settings = ref(null);
const usage = ref(null);
const compare = ref([]);
const loading = ref(true);
const savingKey = ref('');

const BOOL_SETTINGS = [
  'ai_enabled',
  'handoff_on_validation_fail',
  'langsmith_enabled',
];
const NUMBER_SETTINGS = [
  'debounce_seconds',
  'history_limit',
  'max_tool_iterations',
  'main_timeout_seconds',
  'aux_timeout_seconds',
  'order_auth_max_failures',
];

const load = async () => {
  loading.value = true;
  try {
    const [healthRes, settingsRes, usageRes, compareRes] = await Promise.all([
      AIApi.get('health'),
      AIApi.get('settings'),
      AIApi.get('stats/usage', { days: 30 }),
      AIApi.get('stats/models/compare', { days: 30 }),
    ]);
    health.value = healthRes.data;
    settings.value = settingsRes.data;
    usage.value = usageRes.data;
    compare.value = compareRes.data;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const saveSetting = async (key, value) => {
  savingKey.value = key;
  try {
    const { data } = await AIApi.patch('settings', {
      values: { [key]: value },
    });
    settings.value = data;
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  } finally {
    savingKey.value = '';
  }
};

const healthBadge = ok =>
  ok ? 'bg-n-teal-3 text-n-teal-11' : 'bg-n-ruby-3 text-n-ruby-11';

onMounted(load);
</script>

<template>
  <div v-if="loading" class="py-8 text-sm text-n-slate-11">
    {{ t('BEHDASHTIK_AI.COMMON.LOADING') }}
  </div>
  <div v-else-if="!health" class="py-8 text-sm text-n-ruby-11">
    {{ t('BEHDASHTIK_AI.SERVICE_DOWN') }}
  </div>
  <div v-else class="flex flex-col gap-6 pb-8">
    <!-- global toggle -->
    <div
      class="flex items-center justify-between p-4 border rounded-xl border-n-weak"
    >
      <div>
        <p class="text-sm font-medium text-n-slate-12">
          {{ t('BEHDASHTIK_AI.OVERVIEW.AI_ENABLED') }}
        </p>
        <p class="text-xs text-n-slate-11">
          {{ t('BEHDASHTIK_AI.OVERVIEW.AI_ENABLED_HINT') }}
        </p>
      </div>
      <button
        class="px-4 py-1.5 text-sm rounded-lg"
        :class="
          settings.ai_enabled
            ? 'bg-n-teal-9 text-white'
            : 'bg-n-ruby-9 text-white'
        "
        :disabled="savingKey === 'ai_enabled'"
        @click="saveSetting('ai_enabled', !settings.ai_enabled)"
      >
        {{
          settings.ai_enabled
            ? t('BEHDASHTIK_AI.COMMON.ENABLED')
            : t('BEHDASHTIK_AI.COMMON.DISABLED')
        }}
      </button>
    </div>

    <!-- health -->
    <div>
      <h3 class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH') }}
      </h3>
      <div class="flex flex-wrap gap-2">
        <span
          class="px-2 py-1 text-xs rounded-lg"
          :class="healthBadge(health.db)"
        >
          {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH_DB') }}
        </span>
        <span
          class="px-2 py-1 text-xs rounded-lg"
          :class="healthBadge(health.hub && health.hub.ok)"
        >
          {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH_HUB') }}
          <template
            v-if="health.hub && health.hub.mirror_stale_seconds != null"
          >
            {{ `· ${health.hub.mirror_stale_seconds}s` }}
          </template>
        </span>
        <span
          v-if="health.main_hub"
          class="px-2 py-1 text-xs rounded-lg"
          :class="healthBadge(health.main_hub.ok)"
        >
          {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH_HUB_MAIN') }}
          <template v-if="health.main_hub.mirror_stale_seconds != null">
            {{ `· ${health.main_hub.mirror_stale_seconds}s` }}
          </template>
        </span>
        <span
          class="px-2 py-1 text-xs rounded-lg"
          :class="healthBadge(health.chatwoot && health.chatwoot.ok)"
        >
          {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH_CHATWOOT') }}
        </span>
        <span
          class="px-2 py-1 text-xs rounded-lg"
          :class="healthBadge(health.telegram)"
        >
          {{ t('BEHDASHTIK_AI.OVERVIEW.HEALTH_TELEGRAM') }}
        </span>
      </div>
    </div>

    <!-- usage tiles -->
    <div>
      <h3 class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.OVERVIEW.USAGE_30D') }}
      </h3>
      <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div class="p-3 border rounded-xl border-n-weak">
          <p class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.OVERVIEW.RUNS') }}
          </p>
          <p class="text-xl text-n-slate-12">{{ usage.totals.runs || 0 }}</p>
        </div>
        <div class="p-3 border rounded-xl border-n-weak">
          <p class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.OVERVIEW.COST') }}
          </p>
          <p class="text-xl text-n-slate-12">
            {{ Number(usage.totals.cost_usd || 0).toFixed(4) }}
          </p>
        </div>
        <div class="p-3 border rounded-xl border-n-weak">
          <p class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.OVERVIEW.AVG_LATENCY') }}
          </p>
          <p class="text-xl text-n-slate-12">
            {{ Math.round(usage.totals.avg_latency_ms || 0) }}
          </p>
        </div>
        <div class="p-3 border rounded-xl border-n-weak">
          <p class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.OVERVIEW.HANDOFFS') }}
          </p>
          <p class="text-xl text-n-slate-12">
            {{ usage.totals.handoffs || 0 }}
          </p>
        </div>
      </div>
    </div>

    <!-- model comparison -->
    <div v-if="compare.length">
      <h3 class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.OVERVIEW.BY_MODEL') }}
      </h3>
      <div class="overflow-x-auto border rounded-xl border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-n-slate-11 border-b border-n-weak">
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_MODEL') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_RUNS') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_COST') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_LATENCY') }}</th>
              <th class="p-2">
                {{ t('BEHDASHTIK_AI.OVERVIEW.COL_HANDOFFS') }}
              </th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_EVALS') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.OVERVIEW.COL_SCORE') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in compare"
              :key="row.model"
              class="border-b border-n-weak text-n-slate-12"
            >
              <td class="p-2">{{ row.model }}</td>
              <td class="p-2">{{ row.runs }}</td>
              <td class="p-2">{{ Number(row.cost_usd || 0).toFixed(4) }}</td>
              <td class="p-2">{{ Math.round(row.avg_latency_ms || 0) }}</td>
              <td class="p-2">{{ row.handoffs }}</td>
              <td class="p-2">{{ row.eval_count }}</td>
              <td class="p-2">
                {{ row.avg_score == null ? '—' : row.avg_score }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- runtime settings -->
    <div>
      <h3 class="text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.OVERVIEW.SETTINGS') }}
      </h3>
      <p class="mb-2 text-xs text-n-slate-11">
        {{ t('BEHDASHTIK_AI.OVERVIEW.SETTINGS_HINT') }}
      </p>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label
          v-for="key in NUMBER_SETTINGS"
          :key="key"
          class="flex items-center justify-between gap-2 p-3 text-sm border rounded-xl border-n-weak"
        >
          <span class="text-n-slate-12">
            {{ t(`BEHDASHTIK_AI.SETTING_LABELS.${key}`) }}
          </span>
          <input
            v-model.number="settings[key]"
            type="number"
            class="w-24 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
            @change="saveSetting(key, settings[key])"
          />
        </label>
        <label
          v-for="key in BOOL_SETTINGS.filter(k => k !== 'ai_enabled')"
          :key="key"
          class="flex items-center justify-between gap-2 p-3 text-sm border rounded-xl border-n-weak"
        >
          <span class="text-n-slate-12">
            {{ t(`BEHDASHTIK_AI.SETTING_LABELS.${key}`) }}
          </span>
          <input
            v-model="settings[key]"
            type="checkbox"
            class="w-4 h-4"
            @change="saveSetting(key, settings[key])"
          />
        </label>
      </div>
    </div>
  </div>
</template>
