<script>
import { mapGetters } from 'vuex';
import { getContrastingTextColor } from '@chatwoot/utils';
import { IFrameHelper } from 'widget/helpers/utils';

export default {
  components: {},
  props: {
    action: {
      type: Object,
      default: () => {},
    },
  },
  computed: {
    ...mapGetters({
      widgetColor: 'appConfig/getWidgetColor',
    }),
    // Behdashtik: an action may carry its own brand colour (e.g. the Basalam
    // button); everything else keeps following the widget colour.
    buttonColor() {
      return this.action.color || this.widgetColor;
    },
    textColor() {
      return getContrastingTextColor(this.buttonColor);
    },
    isLink() {
      return this.action.type === 'link';
    },
  },
  methods: {
    onClick() {
      if (this.action.type === 'postback') {
        // Send message to parent iframe
        if (IFrameHelper.isIFrame()) {
          IFrameHelper.sendMessage({
            event: 'postback',
            data: { payload: this.action.payload },
          });
        }
      }
    },
  },
};
</script>

<template>
  <a
    v-if="isLink"
    :key="action.uri"
    class="action-button button"
    :href="action.uri"
    :style="{
      background: buttonColor,
      borderColor: buttonColor,
      color: textColor,
    }"
    target="_blank"
    rel="noopener nofollow noreferrer"
  >
    {{ action.text }}
  </a>
  <button
    v-else
    :key="action.payload"
    class="action-button button !bg-n-background dark:!bg-n-alpha-black1 text-n-brand"
    :style="{ borderColor: buttonColor, color: buttonColor }"
    @click="onClick"
  >
    {{ action.text }}
  </button>
</template>

<style scoped lang="scss">
.action-button {
  @apply items-center rounded-md inline-flex text-xs font-medium justify-center px-2 py-1 grow;
  min-height: 1.75rem;
  flex-basis: 6rem;
}
</style>
