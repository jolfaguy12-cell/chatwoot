<script setup>
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

import Overview from './pages/Overview.vue';
import ProvidersModels from './pages/ProvidersModels.vue';
import Prompts from './pages/Prompts.vue';
import ContentGaps from './pages/ContentGaps.vue';
import Responses from './pages/Responses.vue';
import Handoffs from './pages/Handoffs.vue';
import Logs from './pages/Logs.vue';
import TestChat from './pages/TestChat.vue';

const { t } = useI18n();

const tabs = [
  { key: 'overview', component: Overview },
  { key: 'models', component: ProvidersModels },
  { key: 'prompts', component: Prompts },
  { key: 'gaps', component: ContentGaps },
  { key: 'responses', component: Responses },
  { key: 'handoffs', component: Handoffs },
  { key: 'logs', component: Logs },
  { key: 'test', component: TestChat },
];
const activeTab = ref('overview');
const activeComponent = () =>
  tabs.find(tab => tab.key === activeTab.value).component;
</script>

<template>
  <div class="flex flex-col w-full gap-4">
    <div>
      <h1 class="text-2xl font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.TITLE') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('BEHDASHTIK_AI.DESCRIPTION') }}
      </p>
    </div>
    <div class="flex flex-wrap gap-1 border-b border-n-weak">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="px-3 py-2 text-sm rounded-t-lg transition-colors"
        :class="
          activeTab === tab.key
            ? 'text-n-blue-text border-b-2 border-n-brand font-medium'
            : 'text-n-slate-11 hover:text-n-slate-12'
        "
        @click="activeTab = tab.key"
      >
        {{ t(`BEHDASHTIK_AI.TABS.${tab.key.toUpperCase()}`) }}
      </button>
    </div>
    <component :is="activeComponent()" />
  </div>
</template>
