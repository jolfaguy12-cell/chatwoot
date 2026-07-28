<script setup>
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const message = ref('');
const productSlug = ref('');
const running = ref(false);
const result = ref(null);

const run = async () => {
  if (!message.value.trim()) return;
  running.value = true;
  result.value = null;
  try {
    const { data } = await AIApi.post('testchat', {
      text: message.value,
      product_slug: productSlug.value,
    });
    result.value = data;
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  } finally {
    running.value = false;
  }
};
</script>

<template>
  <div class="flex flex-col max-w-2xl gap-3 pb-8">
    <p class="text-xs text-n-slate-11">{{ t('BEHDASHTIK_AI.TEST.HINT') }}</p>
    <label class="text-sm text-n-slate-12">
      {{ t('BEHDASHTIK_AI.TEST.MESSAGE') }}
      <textarea
        v-model="message"
        dir="rtl"
        rows="3"
        class="w-full p-2 mt-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
      />
    </label>
    <label class="text-sm text-n-slate-12">
      {{ t('BEHDASHTIK_AI.TEST.PRODUCT_SLUG') }}
      <input
        v-model="productSlug"
        class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
      />
    </label>
    <div>
      <button
        class="px-4 py-1.5 text-sm text-white rounded-lg bg-n-brand"
        :disabled="running"
        @click="run"
      >
        {{
          running
            ? t('BEHDASHTIK_AI.COMMON.LOADING')
            : t('BEHDASHTIK_AI.TEST.RUN')
        }}
      </button>
    </div>
    <div v-if="result" class="p-3 border rounded-xl border-n-weak">
      <p class="text-xs text-n-slate-11">
        {{ t('BEHDASHTIK_AI.TEST.OUTCOME') }}: {{ result.outcome }} ·
        {{ t('BEHDASHTIK_AI.TEST.INTENT') }}: {{ result.intent }} ·
        {{ t('BEHDASHTIK_AI.TEST.COST') }}:
        {{ Number(result.cost_usd || 0).toFixed(5) }} ·
        {{ `${t('BEHDASHTIK_AI.TEST.LATENCY')}: ${result.latency_ms}ms` }}
      </p>
      <p class="text-xs text-n-slate-11">
        {{ t('BEHDASHTIK_AI.TEST.MODELS') }}:
        {{ (result.models || []).join(', ') }}
      </p>
      <p
        class="p-2 mt-2 text-sm whitespace-pre-wrap rounded-lg bg-n-alpha-1 text-n-slate-12"
        dir="rtl"
      >
        {{ result.final_text }}
      </p>
      <template v-if="result.tool_calls && result.tool_calls.length">
        <h4 class="mt-2 text-xs text-n-slate-11">
          {{ t('BEHDASHTIK_AI.TEST.TOOLS') }}
        </h4>
        <p
          v-for="(call, index) in result.tool_calls"
          :key="index"
          class="font-mono text-xs text-n-slate-12"
        >
          {{
            `${call.tool}(${JSON.stringify(call.args)}) → ${
              call.ok ? 'ok' : 'fail'
            }`
          }}
        </p>
      </template>
    </div>
  </div>
</template>
