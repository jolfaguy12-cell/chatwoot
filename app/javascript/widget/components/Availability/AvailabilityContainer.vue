<script setup>
import { computed, toRef } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store.js';
import GroupedAvatars from 'widget/components/GroupedAvatars.vue';
import AvailabilityText from './AvailabilityText.vue';
import { useAvailability } from 'widget/composables/useAvailability';

const props = defineProps({
  agents: {
    type: Array,
    default: () => [],
  },
  showHeader: {
    type: Boolean,
    default: true,
  },
  showAvatars: {
    type: Boolean,
    default: true,
  },
  textClasses: {
    type: String,
    default: '',
  },
});

const { t } = useI18n();

const availableMessage = useMapGetter('appConfig/getAvailableMessage');
const unavailableMessage = useMapGetter('appConfig/getUnavailableMessage');

// Pass toRef(props, 'agents') instead of props.agents to maintain reactivity
// when the parent component's agents prop updates (e.g., after API response)
const {
  currentTime,
  hasOnlineAgents,
  isOnline,
  inboxConfig,
  isInWorkingHours,
} = useAvailability(toRef(props, 'agents'));

const workingHours = computed(() => inboxConfig.value.workingHours || []);
const workingHoursEnabled = computed(
  () => inboxConfig.value.workingHoursEnabled || false
);
const utcOffset = computed(
  () => inboxConfig.value.utcOffset || inboxConfig.value.timezone || 'UTC'
);
const replyTime = computed(
  () => inboxConfig.value.replyTime || 'in_a_few_minutes'
);

// If online or in working hours
const isAvailable = computed(
  () => isOnline.value || (workingHoursEnabled.value && isInWorkingHours.value)
);

const headerText = computed(() =>
  isAvailable.value
    ? availableMessage.value || t('TEAM_AVAILABILITY.ONLINE')
    : unavailableMessage.value || t('TEAM_AVAILABILITY.OFFLINE')
);
</script>

<template>
  <div class="flex items-center gap-3">
    <GroupedAvatars v-if="showAvatars && isOnline" :users="agents" />

    <div class="flex flex-col flex-1 min-w-0 gap-1">
      <div
        v-if="showHeader"
        class="flex items-center gap-2 font-medium text-n-slate-12"
      >
        <span>{{ headerText }}</span>
        <span
          v-if="isAvailable"
          class="relative flex size-2 shrink-0"
          aria-hidden="true"
        >
          <span
            class="absolute inline-flex w-full h-full rounded-full opacity-75 bg-green-500 animate-ping"
          />
          <span class="relative inline-flex rounded-full size-2 bg-green-500" />
        </span>
      </div>

      <AvailabilityText
        :time="currentTime"
        :utc-offset="utcOffset"
        :working-hours="workingHours"
        :working-hours-enabled="workingHoursEnabled"
        :has-online-agents="hasOnlineAgents"
        :reply-time="replyTime"
        :is-online="isOnline"
        :is-in-working-hours="isInWorkingHours"
        :class="textClasses"
        class="text-n-slate-11"
      />
    </div>
  </div>
</template>
