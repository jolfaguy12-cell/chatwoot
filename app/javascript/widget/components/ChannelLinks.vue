<script setup>
import { computed } from 'vue';
import { CHANNEL_LINK_ICONS } from 'widget/helpers/channelLinkIcons';

const props = defineProps({
  links: { type: Array, default: () => [] },
});

// `enabled` is absent on links saved before the toggle existed — treat those as visible
const visibleLinks = computed(() =>
  props.links.filter(
    link => link && link.label && link.url && link.enabled !== false
  )
);

const iconFor = link =>
  CHANNEL_LINK_ICONS[link.icon] || CHANNEL_LINK_ICONS.generic;

const colorFor = link => link.color || iconFor(link).color;
</script>

<template>
  <div class="flex flex-col gap-2 w-full empty:hidden">
    <a
      v-for="(link, index) in visibleLinks"
      :key="`${link.url}-${index}`"
      :href="link.url"
      target="_blank"
      rel="noreferrer noopener"
      class="flex items-center gap-3 w-full px-4 py-3 no-underline transition-shadow shadow-sm outline outline-1 outline-n-container rounded-xl bg-n-background dark:bg-n-solid-2 hover:shadow-md"
    >
      <span
        class="flex items-center justify-center rounded-lg shrink-0 size-9"
        :style="{ backgroundColor: colorFor(link) }"
      >
        <svg
          class="size-5"
          viewBox="0 0 24 24"
          fill="#ffffff"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <path :d="iconFor(link).path" />
        </svg>
      </span>
      <span class="text-sm font-medium truncate text-n-slate-12">
        {{ link.label }}
      </span>
    </a>
  </div>
</template>
