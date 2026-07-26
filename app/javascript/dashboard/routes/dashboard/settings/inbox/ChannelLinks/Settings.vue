<script setup>
import { ref, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useStore } from 'vuex';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import Button from 'dashboard/components-next/button/Button.vue';
import { CHANNEL_LINK_ICON_NAMES } from 'widget/helpers/channelLinkIcons';

const props = defineProps({
  inbox: {
    type: Object,
    default: () => ({}),
  },
});

const { t } = useI18n();
const store = useStore();
const uiFlags = useMapGetter('inboxes/getUIFlags');

const links = ref([]);

const setDefaults = () => {
  const { channel_links: channelLinks = [] } = props.inbox;
  // Clone so edits stay local until the form is saved.
  // `enabled` is absent on links saved before the toggle existed — default them to visible.
  links.value = (channelLinks || []).map(link => ({
    ...link,
    enabled: link.enabled !== false,
  }));
};

const addLink = () => {
  links.value.push({
    label: '',
    url: '',
    icon: 'generic',
    color: '',
    enabled: true,
  });
};

const removeLink = index => {
  links.value.splice(index, 1);
};

const moveLink = (index, offset) => {
  const target = index + offset;
  if (target < 0 || target >= links.value.length) return;
  const [link] = links.value.splice(index, 1);
  links.value.splice(target, 0, link);
};

const updateInbox = async () => {
  try {
    await store.dispatch('inboxes/updateInbox', {
      id: props.inbox.id,
      formData: false,
      channel: {
        channel_links: links.value.filter(link => link.label && link.url),
      },
    });
    useAlert(t('INBOX_MGMT.EDIT.API.SUCCESS_MESSAGE'));
  } catch (error) {
    useAlert(error.message || t('INBOX_MGMT.EDIT.API.ERROR_MESSAGE'));
  }
};

onMounted(setDefaults);
watch(() => props.inbox.id, setDefaults);
</script>

<template>
  <div class="mx-6 mb-6 max-w-4xl">
    <p class="mt-4 mb-4 text-sm text-n-slate-11">
      {{ $t('INBOX_MGMT.CHANNEL_LINKS.DESCRIPTION') }}
    </p>

    <div v-if="links.length" class="flex flex-col gap-3">
      <div
        v-for="(link, index) in links"
        :key="index"
        class="flex flex-wrap items-end gap-3 p-3 border border-solid rounded-lg border-n-weak"
      >
        <label class="flex flex-col flex-1 min-w-40 gap-1 mb-0 text-sm">
          {{ $t('INBOX_MGMT.CHANNEL_LINKS.LABEL') }}
          <input
            v-model="link.label"
            type="text"
            class="mb-0"
            :placeholder="$t('INBOX_MGMT.CHANNEL_LINKS.LABEL_PLACEHOLDER')"
          />
        </label>

        <label class="flex flex-col flex-1 min-w-56 gap-1 mb-0 text-sm">
          {{ $t('INBOX_MGMT.CHANNEL_LINKS.URL') }}
          <input
            v-model="link.url"
            type="text"
            class="mb-0"
            :placeholder="$t('INBOX_MGMT.CHANNEL_LINKS.URL_PLACEHOLDER')"
          />
        </label>

        <label class="flex flex-col gap-1 mb-0 text-sm w-36">
          {{ $t('INBOX_MGMT.CHANNEL_LINKS.ICON') }}
          <select v-model="link.icon" class="mb-0">
            <option v-for="name in CHANNEL_LINK_ICON_NAMES" :key="name">
              {{ name }}
            </option>
          </select>
        </label>

        <label class="flex flex-col gap-1 mb-0 text-sm w-28">
          {{ $t('INBOX_MGMT.CHANNEL_LINKS.COLOR') }}
          <input
            v-model="link.color"
            type="text"
            class="mb-0"
            :placeholder="$t('INBOX_MGMT.CHANNEL_LINKS.COLOR_PLACEHOLDER')"
          />
        </label>

        <label class="flex items-center gap-2 mb-0 text-sm pb-1.5">
          <input v-model="link.enabled" type="checkbox" class="mb-0" />
          {{ $t('INBOX_MGMT.CHANNEL_LINKS.VISIBLE') }}
        </label>

        <div class="flex items-center gap-1 pb-1">
          <Button
            icon="i-lucide-arrow-up"
            variant="ghost"
            size="sm"
            :disabled="index === 0"
            @click="moveLink(index, -1)"
          />
          <Button
            icon="i-lucide-arrow-down"
            variant="ghost"
            size="sm"
            :disabled="index === links.length - 1"
            @click="moveLink(index, 1)"
          />
          <Button
            icon="i-lucide-trash-2"
            variant="ghost"
            color="ruby"
            size="sm"
            @click="removeLink(index)"
          />
        </div>
      </div>
    </div>

    <p v-else class="text-sm text-n-slate-11">
      {{ $t('INBOX_MGMT.CHANNEL_LINKS.EMPTY') }}
    </p>

    <div class="flex items-center gap-2 mt-4">
      <Button
        :label="$t('INBOX_MGMT.CHANNEL_LINKS.ADD')"
        icon="i-lucide-plus"
        variant="outline"
        size="sm"
        @click="addLink"
      />
      <Button
        :label="$t('INBOX_MGMT.CHANNEL_LINKS.SAVE')"
        size="sm"
        :is-loading="uiFlags.isUpdating"
        @click="updateInbox"
      />
    </div>
  </div>
</template>
