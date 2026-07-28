<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const rows = ref([]);
const total = ref(0);
const page = ref(1);
const loading = ref(true);
const kind = ref('');
const intent = ref('');
const model = ref('');
const sort = ref('created_at');
const detail = ref(null);
const dimensions = ref([]);
const evalForm = ref({});
const evalComment = ref('');
const evalCorrected = ref('');
const runningTests = ref(false);

const KINDS = ['reply', 'refuse', 'clarify', 'handoff', 'stt'];
const INTENTS = ['product', 'order', 'policy', 'greeting', 'human', 'other'];

const load = async () => {
  loading.value = true;
  try {
    const { data } = await AIApi.get('responses', {
      kind: kind.value,
      intent: intent.value,
      model: model.value,
      sort: sort.value,
      page: page.value,
    });
    rows.value = data.data;
    total.value = data.total;
    dimensions.value = data.eval_dimensions || [];
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const openDetail = async row => {
  const { data } = await AIApi.get(`responses/${row.id}`);
  detail.value = data;
  evalForm.value = {};
  evalComment.value = '';
  evalCorrected.value = '';
};

const submitEvaluation = async () => {
  try {
    await AIApi.post(`responses/${detail.value.id}/evaluations`, {
      dimensions: Object.fromEntries(
        Object.entries(evalForm.value).filter(([, score]) => score)
      ),
      comment: evalComment.value,
      corrected_response: evalCorrected.value,
    });
    useAlert(t('BEHDASHTIK_AI.RESPONSES.EVAL_SAVED'));
    await openDetail(detail.value);
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const saveTestCase = async () => {
  try {
    await AIApi.post(`responses/${detail.value.id}/save_test_case`, {
      preferred_answer: evalCorrected.value,
      expectation_notes: evalComment.value,
    });
    useAlert(t('BEHDASHTIK_AI.RESPONSES.TEST_CASE_SAVED'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const runAllTests = async () => {
  runningTests.value = true;
  try {
    await AIApi.post('test_runs', { test_case_ids: [], auto_eval: true });
    useAlert(t('BEHDASHTIK_AI.RESPONSES.TESTS_DONE'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  } finally {
    runningTests.value = false;
  }
};

onMounted(load);
</script>

<template>
  <div class="flex flex-col gap-3 pb-8">
    <p class="text-xs text-n-slate-11">
      {{ t('BEHDASHTIK_AI.RESPONSES.HINT') }}
    </p>
    <div class="flex flex-wrap gap-2">
      <select
        v-model="kind"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="">
          {{ t('BEHDASHTIK_AI.RESPONSES.FILTER_ALL_KINDS') }}
        </option>
        <option v-for="item in KINDS" :key="item" :value="item">
          {{ item }}
        </option>
      </select>
      <select
        v-model="intent"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="">
          {{ t('BEHDASHTIK_AI.RESPONSES.FILTER_ALL_INTENTS') }}
        </option>
        <option v-for="item in INTENTS" :key="item" :value="item">
          {{ item }}
        </option>
      </select>
      <input
        v-model="model"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        :placeholder="t('BEHDASHTIK_AI.RESPONSES.FILTER_MODEL')"
        @keyup.enter="load"
      />
      <select
        v-model="sort"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="created_at">
          {{ t('BEHDASHTIK_AI.RESPONSES.SORT_NEWEST') }}
        </option>
        <option value="cost_usd">
          {{ t('BEHDASHTIK_AI.RESPONSES.SORT_COST') }}
        </option>
        <option value="latency_ms">
          {{ t('BEHDASHTIK_AI.RESPONSES.SORT_LATENCY') }}
        </option>
      </select>
      <button
        class="px-3 py-1 text-sm border rounded-lg border-n-weak text-n-slate-12"
        @click="load"
      >
        {{ t('BEHDASHTIK_AI.COMMON.REFRESH') }}
      </button>
      <button
        class="px-3 py-1 text-sm border rounded-lg border-n-weak text-n-slate-12"
        :disabled="runningTests"
        @click="runAllTests"
      >
        {{ t('BEHDASHTIK_AI.RESPONSES.RUN_TESTS') }}
      </button>
    </div>

    <div v-if="loading" class="py-8 text-sm text-n-slate-11">
      {{ t('BEHDASHTIK_AI.COMMON.LOADING') }}
    </div>
    <div v-else class="overflow-x-auto border rounded-xl border-n-weak">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left border-b text-n-slate-11 border-n-weak">
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_TIME') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_CONV') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_KIND') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_INTENT') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_MODEL') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_COST') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_LATENCY') }}</th>
            <th class="p-2">{{ t('BEHDASHTIK_AI.RESPONSES.COL_INPUT') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="border-b cursor-pointer border-n-weak text-n-slate-12 hover:bg-n-alpha-1"
            @click="openDetail(row)"
          >
            <td class="p-2 text-xs whitespace-nowrap">
              {{ new Date(row.created_at).toLocaleString() }}
            </td>
            <td class="p-2">#{{ row.conversation_id }}</td>
            <td class="p-2">{{ row.kind }}</td>
            <td class="p-2">{{ row.intent }}</td>
            <td class="p-2 text-xs">{{ row.model_used }}</td>
            <td class="p-2 text-xs">{{ Number(row.cost_usd).toFixed(5) }}</td>
            <td class="p-2 text-xs">{{ row.latency_ms }}</td>
            <td class="p-2 max-w-64 truncate" dir="auto">
              {{ row.input_text || row.output_text }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- detail drawer -->
    <div
      v-if="detail"
      class="fixed inset-0 z-40 flex justify-end bg-black/30"
      @click.self="detail = null"
    >
      <div
        class="w-full h-full max-w-2xl p-4 overflow-y-auto bg-n-surface-1 text-n-slate-12"
      >
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-medium">
            {{ t('BEHDASHTIK_AI.RESPONSES.DETAIL') }} #{{ detail.id }} ·
            {{ detail.kind }} · {{ detail.model_used }}
          </h3>
          <button
            class="px-2 py-1 text-sm border rounded-lg border-n-weak"
            @click="detail = null"
          >
            {{ t('BEHDASHTIK_AI.COMMON.CLOSE') }}
          </button>
        </div>
        <h4 class="mt-3 text-xs text-n-slate-11">
          {{ t('BEHDASHTIK_AI.RESPONSES.INPUT') }}
        </h4>
        <p class="p-2 text-sm rounded-lg bg-n-alpha-1" dir="auto">
          {{ detail.input_text }}
        </p>
        <h4 class="mt-3 text-xs text-n-slate-11">
          {{ t('BEHDASHTIK_AI.RESPONSES.OUTPUT') }}
        </h4>
        <p
          class="p-2 text-sm whitespace-pre-wrap rounded-lg bg-n-alpha-1"
          dir="auto"
        >
          {{ detail.output_text }}
        </p>
        <template v-if="detail.tool_calls && detail.tool_calls.length">
          <h4 class="mt-3 text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.RESPONSES.TOOLS') }}
          </h4>
          <p
            v-for="(call, index) in detail.tool_calls"
            :key="index"
            class="font-mono text-xs"
          >
            {{
              `${call.tool}(${JSON.stringify(call.args)}) → ${
                call.ok ? 'ok' : 'fail'
              } ${call.note || ''}`
            }}
          </p>
        </template>
        <template v-if="detail.validation && detail.validation.issues">
          <h4 class="mt-3 text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.RESPONSES.VALIDATION') }}
          </h4>
          <p class="text-xs">{{ JSON.stringify(detail.validation) }}</p>
        </template>
        <template v-if="detail.calls && detail.calls.length">
          <h4 class="mt-3 text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.RESPONSES.CALLS') }}
          </h4>
          <p v-for="call in detail.calls" :key="call.id" class="text-xs">
            {{
              `${call.kind} · ${call.model_used} · ${call.latency_ms}ms · ${Number(
                call.cost_usd
              ).toFixed(6)}$ · ${call.status}`
            }}
          </p>
        </template>

        <template v-if="detail.evaluations && detail.evaluations.length">
          <h4 class="mt-3 text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.RESPONSES.EVAL_EXISTING') }}
          </h4>
          <p
            v-for="evaluation in detail.evaluations"
            :key="evaluation.id"
            class="text-xs"
          >
            {{ evaluation.rater_name }}:
            {{ JSON.stringify(evaluation.dimensions) }}
            {{ evaluation.comment }}
          </p>
        </template>

        <h4 class="mt-4 text-sm font-medium">
          {{ t('BEHDASHTIK_AI.RESPONSES.EVALUATE') }}
        </h4>
        <div class="grid grid-cols-2 gap-2 mt-2">
          <label
            v-for="dimension in dimensions"
            :key="dimension"
            class="flex items-center justify-between gap-1 text-xs"
          >
            <span>{{ dimension }}</span>
            <select
              v-model.number="evalForm[dimension]"
              class="px-1 py-0.5 border rounded border-n-weak bg-n-surface-1"
            >
              <option :value="undefined">—</option>
              <option
                v-for="score in [1, 2, 3, 4, 5]"
                :key="score"
                :value="score"
              >
                {{ score }}
              </option>
            </select>
          </label>
        </div>
        <input
          v-model="evalComment"
          class="w-full px-2 py-1 mt-2 text-sm border rounded-lg border-n-weak bg-n-surface-1"
          :placeholder="t('BEHDASHTIK_AI.RESPONSES.EVAL_COMMENT')"
        />
        <textarea
          v-model="evalCorrected"
          dir="auto"
          rows="3"
          class="w-full px-2 py-1 mt-2 text-sm border rounded-lg border-n-weak bg-n-surface-1"
          :placeholder="t('BEHDASHTIK_AI.RESPONSES.EVAL_CORRECTED')"
        />
        <div class="flex gap-2 mt-2">
          <button
            class="px-3 py-1.5 text-sm text-white rounded-lg bg-n-brand"
            @click="submitEvaluation"
          >
            {{ t('BEHDASHTIK_AI.RESPONSES.EVAL_SUBMIT') }}
          </button>
          <button
            class="px-3 py-1.5 text-sm border rounded-lg border-n-weak"
            @click="saveTestCase"
          >
            {{ t('BEHDASHTIK_AI.RESPONSES.SAVE_TEST_CASE') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
