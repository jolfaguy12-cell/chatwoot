<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const gaps = ref([]);
const total = ref(0);
const page = ref(1);
const loading = ref(true);
const q = ref('');
const status = ref('');
const category = ref('');
const sort = ref('last_seen');
const retesting = ref(null);
const expanded = ref(null);

const STATUSES = ['open', 'in_progress', 'resolved', 'wont_fix'];
const CATEGORIES = ['expiry', 'usage', 'specs', 'policy', 'other'];

const statusLabel = value =>
  t(`BEHDASHTIK_AI.GAPS.STATUS_${value.toUpperCase()}`);

const load = async () => {
  loading.value = true;
  try {
    const { data } = await AIApi.get('content_gaps', {
      q: q.value,
      status: status.value,
      category: category.value,
      sort: sort.value,
      page: page.value,
    });
    gaps.value = data.data;
    total.value = data.total;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const saveGap = async gap => {
  try {
    await AIApi.patch(`content_gaps/${gap.id}`, {
      status: gap.status,
      priority: gap.priority,
      owner: gap.owner,
      notes: gap.notes,
    });
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const retest = async gap => {
  retesting.value = gap.id;
  try {
    const { data } = await AIApi.post(`content_gaps/${gap.id}/retest`);
    gap.last_retest = data;
    useAlert(
      data.answered
        ? t('BEHDASHTIK_AI.GAPS.RETEST_ANSWERED')
        : t('BEHDASHTIK_AI.GAPS.RETEST_STILL_MISSING')
    );
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  } finally {
    retesting.value = null;
  }
};

onMounted(load);
</script>

<template>
  <div class="flex flex-col gap-3 pb-8">
    <p class="text-xs text-n-slate-11">{{ t('BEHDASHTIK_AI.GAPS.HINT') }}</p>
    <div class="flex flex-wrap gap-2">
      <input
        v-model="q"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        :placeholder="t('BEHDASHTIK_AI.COMMON.SEARCH')"
        @keyup.enter="page = 1 && load()"
      />
      <select
        v-model="status"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="">
          {{ t('BEHDASHTIK_AI.GAPS.FILTER_ALL_STATUSES') }}
        </option>
        <option v-for="item in STATUSES" :key="item" :value="item">
          {{ statusLabel(item) }}
        </option>
      </select>
      <select
        v-model="category"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="">
          {{ t('BEHDASHTIK_AI.GAPS.FILTER_ALL_CATEGORIES') }}
        </option>
        <option v-for="item in CATEGORIES" :key="item" :value="item">
          {{ item }}
        </option>
      </select>
      <select
        v-model="sort"
        class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
        @change="load"
      >
        <option value="last_seen">
          {{ t('BEHDASHTIK_AI.GAPS.LAST_SEEN') }}
        </option>
        <option value="occurrences">
          {{ t('BEHDASHTIK_AI.GAPS.OCCURRENCES') }}
        </option>
        <option value="priority">{{ t('BEHDASHTIK_AI.GAPS.PRIORITY') }}</option>
      </select>
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
    <div v-else-if="!gaps.length" class="py-8 text-sm text-n-slate-11">
      {{ t('BEHDASHTIK_AI.COMMON.EMPTY') }}
    </div>
    <div v-else class="flex flex-col gap-2">
      <div
        v-for="gap in gaps"
        :key="gap.id"
        class="p-3 border rounded-xl border-n-weak"
      >
        <div class="flex flex-wrap items-center gap-2">
          <button
            class="flex-1 min-w-64 text-sm text-right text-n-slate-12"
            dir="auto"
            @click="expanded = expanded === gap.id ? null : gap.id"
          >
            {{ gap.question }}
          </button>
          <span
            class="px-2 py-0.5 text-xs rounded bg-n-alpha-1 text-n-slate-11"
          >
            {{ gap.category }}
          </span>
          <span class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.GAPS.OCCURRENCES') }}: {{ gap.occurrences }}
          </span>
          <select
            v-model="gap.status"
            class="px-2 py-1 text-xs border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
            @change="saveGap(gap)"
          >
            <option v-for="item in STATUSES" :key="item" :value="item">
              {{ statusLabel(item) }}
            </option>
          </select>
          <select
            v-model.number="gap.priority"
            class="px-2 py-1 text-xs border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
            @change="saveGap(gap)"
          >
            <option :value="0">
              {{ t('BEHDASHTIK_AI.GAPS.PRIORITY_NONE') }}
            </option>
            <option :value="1">
              {{ t('BEHDASHTIK_AI.GAPS.PRIORITY_LOW') }}
            </option>
            <option :value="2">
              {{ t('BEHDASHTIK_AI.GAPS.PRIORITY_MEDIUM') }}
            </option>
            <option :value="3">
              {{ t('BEHDASHTIK_AI.GAPS.PRIORITY_HIGH') }}
            </option>
          </select>
          <button
            class="px-2 py-1 text-xs border rounded-lg border-n-weak text-n-slate-12"
            :disabled="retesting === gap.id"
            @click="retest(gap)"
          >
            {{ t('BEHDASHTIK_AI.GAPS.RETEST') }}
          </button>
        </div>
        <div v-if="expanded === gap.id" class="mt-2 text-sm text-n-slate-12">
          <p v-if="gap.product_slug" class="text-xs text-n-slate-11">
            {{ t('BEHDASHTIK_AI.GAPS.PRODUCT') }}: {{ gap.product_slug }}
          </p>
          <p v-if="gap.missing_info" class="mt-1 text-xs" dir="auto">
            {{ gap.missing_info }}
          </p>
          <p
            v-if="gap.example_questions && gap.example_questions.length > 1"
            class="mt-1 text-xs text-n-slate-11"
          >
            {{ t('BEHDASHTIK_AI.GAPS.EXAMPLES') }}:
            {{ gap.example_questions.join(' | ') }}
          </p>
          <div class="flex gap-2 mt-2">
            <input
              v-model="gap.owner"
              class="px-2 py-1 text-xs border rounded-lg border-n-weak bg-n-surface-1"
              :placeholder="t('BEHDASHTIK_AI.GAPS.OWNER')"
            />
            <input
              v-model="gap.notes"
              class="flex-1 px-2 py-1 text-xs border rounded-lg border-n-weak bg-n-surface-1"
              :placeholder="t('BEHDASHTIK_AI.GAPS.NOTES')"
            />
            <button
              class="px-2 py-1 text-xs text-white rounded-lg bg-n-brand"
              @click="saveGap(gap)"
            >
              {{ t('BEHDASHTIK_AI.COMMON.SAVE') }}
            </button>
          </div>
          <p
            v-if="gap.last_retest && gap.last_retest.final_text"
            class="p-2 mt-2 text-xs rounded-lg bg-n-alpha-1"
            dir="auto"
          >
            {{ gap.last_retest.final_text }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
