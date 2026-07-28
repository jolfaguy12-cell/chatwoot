<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const prompts = ref([]);
const loading = ref(true);
const selected = ref(null);
const versions = ref([]);
const draft = ref('');
const notes = ref('');

const load = async () => {
  loading.value = true;
  try {
    const { data } = await AIApi.get('prompts');
    prompts.value = data;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const select = async prompt => {
  selected.value = prompt;
  draft.value = prompt.active_content;
  notes.value = '';
  const { data } = await AIApi.get(`prompts/${prompt.key}/versions`);
  versions.value = data;
};

const saveVersion = async () => {
  try {
    await AIApi.post(`prompts/${selected.value.key}/versions`, {
      content: draft.value,
      notes: notes.value,
      activate: true,
    });
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
    await load();
    const refreshed = prompts.value.find(p => p.key === selected.value.key);
    if (refreshed) await select(refreshed);
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const activate = async version => {
  try {
    await AIApi.post(`prompts/${selected.value.key}/activate/${version}`);
    useAlert(t('BEHDASHTIK_AI.PROMPTS.ACTIVATED'));
    await load();
    const refreshed = prompts.value.find(p => p.key === selected.value.key);
    if (refreshed) await select(refreshed);
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

onMounted(load);
</script>

<template>
  <div v-if="loading" class="py-8 text-sm text-n-slate-11">
    {{ t('BEHDASHTIK_AI.COMMON.LOADING') }}
  </div>
  <div v-else class="flex gap-4 pb-8">
    <div class="w-64 shrink-0">
      <p class="mb-2 text-xs text-n-slate-11">
        {{ t('BEHDASHTIK_AI.PROMPTS.HINT') }}
      </p>
      <button
        v-for="prompt in prompts"
        :key="prompt.key"
        class="block w-full px-3 py-2 mb-1 text-sm text-left rounded-lg"
        :class="
          selected && selected.key === prompt.key
            ? 'bg-n-brand/10 text-n-blue-text'
            : 'text-n-slate-12 hover:bg-n-alpha-1'
        "
        @click="select(prompt)"
      >
        <span class="font-mono text-xs">{{ prompt.key }}</span>
        <span class="block text-xs text-n-slate-11">
          {{ `v${prompt.active_version}` }}
        </span>
      </button>
    </div>
    <div v-if="selected" class="flex-1 min-w-0">
      <p class="mb-1 text-sm font-medium text-n-slate-12">
        {{ selected.key }}
        <span class="text-xs text-n-slate-11">{{
          `— ${selected.description}`
        }}</span>
      </p>
      <textarea
        v-model="draft"
        dir="auto"
        rows="16"
        class="w-full p-3 font-mono text-sm border rounded-xl border-n-weak bg-n-surface-1 text-n-slate-12"
      />
      <div class="flex items-center gap-2 mt-2">
        <input
          v-model="notes"
          class="flex-1 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
          :placeholder="t('BEHDASHTIK_AI.PROMPTS.NOTES')"
        />
        <button
          class="px-3 py-1.5 text-sm text-white rounded-lg bg-n-brand"
          @click="saveVersion"
        >
          {{ t('BEHDASHTIK_AI.PROMPTS.NEW_VERSION') }}
        </button>
      </div>
      <h4 class="mt-4 mb-1 text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.PROMPTS.VERSIONS') }}
      </h4>
      <div
        v-for="version in versions"
        :key="version.version"
        class="flex items-center gap-2 py-1 text-sm border-b border-n-weak text-n-slate-12"
      >
        <span class="font-mono text-xs">{{ `v${version.version}` }}</span>
        <span
          v-if="version.active"
          class="px-1.5 text-xs rounded bg-n-teal-3 text-n-teal-11"
        >
          {{ t('BEHDASHTIK_AI.PROMPTS.ACTIVE_VERSION') }}
        </span>
        <span class="flex-1 text-xs truncate text-n-slate-11">
          {{ version.notes }} · {{ t('BEHDASHTIK_AI.PROMPTS.BY') }}
          {{ version.created_by }}
        </span>
        <button
          v-if="!version.active"
          class="px-2 py-0.5 text-xs border rounded-lg border-n-weak"
          @click="activate(version.version)"
        >
          {{ t('BEHDASHTIK_AI.PROMPTS.ACTIVATE') }}
        </button>
      </div>
    </div>
  </div>
</template>
