<script setup>
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import { forService } from 'dashboard/api/behdashtikAI';
import SectionLayout from '../account/components/SectionLayout.vue';

const props = defineProps({
  // each agent runs its own operator bot, so each needs its own link
  service: { type: String, default: 'site' },
});

const { t } = useI18n();

const AIApi = forService(props.service);
const status = ref(null);
const available = ref(true);
const connecting = ref(false);

const PREFS = ['dashboard', 'telegram', 'both', 'disabled'];

const load = async () => {
  try {
    const { data } = await AIApi.get('telegram/self');
    status.value = data;
  } catch (error) {
    available.value = false;
  }
};

const connect = async () => {
  connecting.value = true;
  try {
    const { data } = await AIApi.post('telegram/link_code');
    if (data.deep_link) {
      window.open(data.deep_link, '_blank', 'noopener');
      // give the operator time to press Start, then re-check periodically
      const poll = setInterval(async () => {
        await load();
        if (status.value && status.value.linked) clearInterval(poll);
      }, 3000);
      setTimeout(() => clearInterval(poll), 120000);
    } else {
      useAlert(t('TELEGRAM_CONNECTION.UNAVAILABLE'));
    }
  } catch (error) {
    useAlert(t('TELEGRAM_CONNECTION.UNAVAILABLE'));
  } finally {
    connecting.value = false;
  }
};

const savePref = async () => {
  try {
    const { data } = await AIApi.patch('telegram/self', {
      pref: status.value.pref,
    });
    status.value = data;
    useAlert(t('TELEGRAM_CONNECTION.PREF_SAVED'));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const disconnect = async () => {
  try {
    await AIApi.delete('telegram/self');
    useAlert(t('TELEGRAM_CONNECTION.DISCONNECTED'));
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

onMounted(load);
</script>

<template>
  <SectionLayout
    v-if="available"
    :title="`${t('TELEGRAM_CONNECTION.TITLE')} — ${t(
      `BEHDASHTIK_AI.SERVICES.${service.toUpperCase()}`
    )}`"
    :description="t('TELEGRAM_CONNECTION.NOTE')"
    with-border
  >
    <div class="flex flex-col gap-3">
      <div v-if="status" class="flex items-center gap-3">
        <span
          class="px-2 py-1 text-xs rounded-lg"
          :class="
            status.linked
              ? 'bg-n-teal-3 text-n-teal-11'
              : 'bg-n-alpha-1 text-n-slate-11'
          "
        >
          {{
            status.linked
              ? t('TELEGRAM_CONNECTION.STATUS_LINKED', {
                  username: status.telegram_username,
                })
              : t('TELEGRAM_CONNECTION.STATUS_NOT_LINKED')
          }}
        </span>
        <button
          v-if="!status.linked"
          class="px-3 py-1.5 text-sm text-white rounded-lg bg-n-brand"
          :disabled="connecting"
          :title="t('TELEGRAM_CONNECTION.CONNECT_HINT')"
          @click="connect"
        >
          {{ t('TELEGRAM_CONNECTION.CONNECT') }}
        </button>
        <button
          v-else
          class="px-3 py-1.5 text-sm border rounded-lg border-n-weak text-n-ruby-11"
          @click="disconnect"
        >
          {{ t('TELEGRAM_CONNECTION.DISCONNECT') }}
        </button>
      </div>
      <div v-if="status && status.linked" class="flex items-center gap-2">
        <span class="text-sm text-n-slate-12">
          {{ t('TELEGRAM_CONNECTION.PREF_LABEL') }}
        </span>
        <select
          v-model="status.pref"
          class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
          @change="savePref"
        >
          <option v-for="pref in PREFS" :key="pref" :value="pref">
            {{ t(`TELEGRAM_CONNECTION.PREF_${pref.toUpperCase()}`) }}
          </option>
        </select>
      </div>
    </div>
  </SectionLayout>
</template>
