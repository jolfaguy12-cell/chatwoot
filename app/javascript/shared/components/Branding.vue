<script>
import { useBranding } from 'shared/composables/useBranding';

const {
  LOGO_THUMBNAIL: logoThumbnail,
  BRAND_NAME: brandName,
  WIDGET_BRAND_URL: widgetBrandURL,
} = window.globalConfig || {};

export default {
  props: {
    disableBranding: {
      type: Boolean,
      default: false,
    },
  },
  setup() {
    const { replaceInstallationName } = useBranding();
    return {
      replaceInstallationName,
    };
  },
  data() {
    return {
      globalConfig: {
        brandName,
        logoThumbnail,
        widgetBrandURL,
      },
    };
  },
  computed: {
    brandRedirectURL() {
      try {
        const referrerHost = this.$store.getters['appConfig/getReferrerHost'];
        const url = new URL(this.globalConfig.widgetBrandURL);
        // tel:/mailto: links are not hierarchical — appending UTM params turns
        // them into a number the dialer cannot parse.
        if (!['http:', 'https:'].includes(url.protocol)) {
          return this.globalConfig.widgetBrandURL;
        }
        if (referrerHost) {
          url.searchParams.set('utm_source', referrerHost);
          url.searchParams.set('utm_medium', 'widget');
        } else {
          url.searchParams.set('utm_medium', 'survey');
        }
        url.searchParams.set('utm_campaign', 'branding');
        return url.toString();
      } catch (e) {
        // Suppressing the error as getter is not defined in some cases
      }
      return '';
    },
  },
};
</script>

<template>
  <div
    v-if="globalConfig.brandName && !disableBranding"
    class="px-0 py-3 flex justify-center"
  >
    <a
      :href="brandRedirectURL"
      rel="noreferrer noopener nofollow"
      target="_blank"
      class="branding--link text-n-slate-11 hover:text-n-slate-12 cursor-pointer text-xs inline-flex grayscale-[1] hover:grayscale-0 hover:opacity-100 opacity-90 no-underline justify-center items-center leading-3"
    >
      <!-- Behdashtik: no logo thumbnail — LOGO_THUMBNAIL still ships the
        Chatwoot mark and the footer is text-only. -->
      <span>
        {{ replaceInstallationName($t('POWERED_BY')) }}
      </span>
    </a>
  </div>
  <div v-else class="p-3" />
</template>
