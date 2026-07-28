<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const handoffs = ref([]);
const operators = ref([]);
const loading = ref(true);

const PREFS = ['dashboard', 'telegram', 'both', 'disabled'];

const load = async () => {
  loading.value = true;
  try {
    const [handoffsRes, operatorsRes] = await Promise.all([
      AIApi.get('handoffs'),
      AIApi.get('operators'),
    ]);
    handoffs.value = handoffsRes.data.data;
    operators.value = operatorsRes.data;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const returnToAI = async handoff => {
  try {
    await AIApi.post(`handoffs/${handoff.id}/return_to_ai`);
    useAlert(t('BEHDASHTIK_AI.HANDOFFS.RETURNED'));
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const saveOperator = async operator => {
  try {
    await AIApi.patch(`operators/${operator.id}`, {
      pref: operator.pref,
      active: operator.active,
    });
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const unlink = async operator => {
  try {
    await AIApi.delete(`operators/${operator.id}`);
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const conversationUrl = id =>
  `${window.location.origin}/app/accounts/${
    window.location.pathname.split('/')[3]
  }/conversations/${id}`;

onMounted(load);
</script>

<template>
  <div v-if="loading" class="py-8 text-sm text-n-slate-11">
    {{ t('BEHDASHTIK_AI.COMMON.LOADING') }}
  </div>
  <div v-else class="flex flex-col gap-6 pb-8">
    <p class="text-xs text-n-slate-11">
      {{ t('BEHDASHTIK_AI.HANDOFFS.HINT') }}
    </p>

    <div class="overflow-x-auto border rounded-xl border-n-weak">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left border-b text-n-slate-11 border-n-weak">
            <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.COL_CONV') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.COL_REASON') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.COL_STATUS') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.COL_CLAIMED') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.COL_CREATED') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ACTIONS') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="handoff in handoffs"
            :key="handoff.id"
            class="border-b border-n-weak text-n-slate-12"
          >
            <td class="p-2">
              <a
                :href="conversationUrl(handoff.conversation_id)"
                class="text-n-blue-text"
                target="_blank"
                rel="noopener noreferrer"
              >
                #{{ handoff.conversation_id }}
              </a>
            </td>
            <td class="p-2 max-w-72">
              <span class="block text-xs">{{ handoff.reason_kind }}</span>
              <span class="block text-xs truncate text-n-slate-11" dir="auto">
                {{ handoff.reason }}
              </span>
            </td>
            <td class="p-2">
              {{
                t(
                  `BEHDASHTIK_AI.HANDOFFS.STATUS_${handoff.status.toUpperCase()}`
                )
              }}
            </td>
            <td class="p-2">{{ handoff.claimed_by || '—' }}</td>
            <td class="p-2 text-xs whitespace-nowrap">
              {{ new Date(handoff.created_at).toLocaleString() }}
            </td>
            <td class="p-2">
              <button
                v-if="['pending', 'claimed'].includes(handoff.status)"
                class="px-2 py-1 text-xs border rounded-lg border-n-weak"
                @click="returnToAI(handoff)"
              >
                {{ t('BEHDASHTIK_AI.HANDOFFS.RETURN_TO_AI') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div>
      <h3 class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.HANDOFFS.OPERATORS') }}
      </h3>
      <div class="overflow-x-auto border rounded-xl border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left border-b text-n-slate-11 border-n-weak">
              <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.OP_NAME') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.OP_TELEGRAM') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.OP_PREF') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.HANDOFFS.OP_ACTIVE') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ACTIONS') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="operator in operators"
              :key="operator.id"
              class="border-b border-n-weak text-n-slate-12"
            >
              <td class="p-2">{{ operator.chatwoot_user_name }}</td>
              <td class="p-2">{{ `@${operator.telegram_username}` }}</td>
              <td class="p-2">
                <select
                  v-model="operator.pref"
                  class="px-2 py-1 text-xs border rounded-lg border-n-weak bg-n-surface-1"
                  @change="saveOperator(operator)"
                >
                  <option v-for="pref in PREFS" :key="pref" :value="pref">
                    {{ t(`BEHDASHTIK_AI.HANDOFFS.PREF_${pref.toUpperCase()}`) }}
                  </option>
                </select>
              </td>
              <td class="p-2">
                <input
                  v-model="operator.active"
                  type="checkbox"
                  @change="saveOperator(operator)"
                />
              </td>
              <td class="p-2">
                <button
                  class="px-2 py-1 text-xs rounded-lg text-n-ruby-11 border border-n-weak"
                  @click="unlink(operator)"
                >
                  {{ t('BEHDASHTIK_AI.HANDOFFS.UNLINK') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
