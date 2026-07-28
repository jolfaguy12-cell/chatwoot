<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const rows = ref([]);
const loading = ref(true);
const action = ref('');

const load = async () => {
  loading.value = true;
  try {
    const { data } = await AIApi.get('audit_log', { action: action.value });
    rows.value = data.data;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const details = row => {
  const parts = [];
  if (row.before && Object.keys(row.before).length)
    parts.push(`before: ${JSON.stringify(row.before)}`);
  if (row.after && Object.keys(row.after).length)
    parts.push(`after: ${JSON.stringify(row.after)}`);
  return parts.join(' → ');
};

onMounted(load);
</script>

<template>
  <div class="flex flex-col gap-3 pb-8">
    <p class="text-xs text-n-slate-11">{{ t('BEHDASHTIK_AI.LOGS.HINT') }}</p>
    <div class="flex gap-2">
      <input
        v-model="action"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        :placeholder="t('BEHDASHTIK_AI.COMMON.SEARCH')"
        @keyup.enter="load"
      />
      <button
        class="px-3 py-1 text-sm border rounded-lg border-n-weak text-n-slate-12"
        @click="load"
      >
        {{ t('BEHDASHTIK_AI.COMMON.REFRESH') }}
      </button>
    </div>
    <div v-if="loading" class="py-8 text-sm text-n-slate-11">
      {{ t('BEHDASHTIK_AI.COMMON.LOADING') }}
    </div>
    <div v-else class="overflow-x-auto border rounded-xl border-n-weak">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left border-b text-n-slate-11 border-n-weak">
            <th class="p-2">{{ t('BEHDASHTIK_AI.LOGS.COL_TIME') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.LOGS.COL_ACTOR') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.LOGS.COL_ACTION') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.LOGS.COL_ENTITY') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.LOGS.COL_DETAILS') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="border-b border-n-weak text-n-slate-12"
          >
            <td class="p-2 text-xs whitespace-nowrap">
              {{ new Date(row.created_at).toLocaleString() }}
            </td>
            <td class="p-2">{{ row.actor }} ({{ row.via }})</td>
            <td class="p-2">{{ row.action }}</td>
            <td class="p-2 text-xs">{{ row.entity }} {{ row.entity_id }}</td>
            <td class="p-2 text-xs max-w-96 truncate">{{ details(row) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
