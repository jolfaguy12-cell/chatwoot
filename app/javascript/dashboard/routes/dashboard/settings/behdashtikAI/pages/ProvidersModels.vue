<script setup>
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import AIApi from 'dashboard/api/behdashtikAI';

const { t } = useI18n();

const providers = ref([]);
const models = ref([]);
const roles = ref([]);
const loading = ref(true);
const testResult = ref({});
const newProvider = ref(null);
const newModel = ref(null);

const modelName = id => {
  const model = models.value.find(m => m.id === id);
  return model ? model.model_name : null;
};
const enabledModels = computed(() => models.value.filter(m => m.enabled));

const load = async () => {
  loading.value = true;
  try {
    const [providersRes, modelsRes, rolesRes] = await Promise.all([
      AIApi.get('providers'),
      AIApi.get('models'),
      AIApi.get('roles'),
    ]);
    providers.value = providersRes.data;
    models.value = modelsRes.data;
    roles.value = rolesRes.data.map(role => ({
      ...role,
      splitModelId: role.traffic_split[0]
        ? role.traffic_split[0].model_id
        : null,
      splitPct: role.traffic_split[0] ? role.traffic_split[0].pct : 0,
    }));
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.SERVICE_DOWN'));
  } finally {
    loading.value = false;
  }
};

const saveProvider = async provider => {
  try {
    if (provider.id) {
      await AIApi.patch(`providers/${provider.id}`, provider);
    } else {
      await AIApi.post('providers', provider);
      newProvider.value = null;
    }
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const testProvider = async provider => {
  testResult.value = { ...testResult.value, [`p${provider.id}`]: '...' };
  const { data } = await AIApi.post(`providers/${provider.id}/test`);
  testResult.value = {
    ...testResult.value,
    [`p${provider.id}`]: data.ok
      ? `${t('BEHDASHTIK_AI.MODELS.TEST_OK')} (${data.latency_ms}ms)`
      : `${t('BEHDASHTIK_AI.MODELS.TEST_FAIL')}: ${data.detail}`,
  };
};

const testModel = async model => {
  testResult.value = { ...testResult.value, [`m${model.id}`]: '...' };
  const { data } = await AIApi.post(`models/${model.id}/test`);
  testResult.value = {
    ...testResult.value,
    [`m${model.id}`]: data.ok
      ? `${t('BEHDASHTIK_AI.MODELS.TEST_OK')} (${data.latency_ms}ms)`
      : `${t('BEHDASHTIK_AI.MODELS.TEST_FAIL')}: ${data.detail}`,
  };
};

const saveModel = async model => {
  try {
    if (model.id) {
      await AIApi.patch(`models/${model.id}`, model);
    } else {
      await AIApi.post('models', model);
      newModel.value = null;
    }
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const deleteModel = async model => {
  try {
    await AIApi.delete(`models/${model.id}`);
    await load();
  } catch (error) {
    useAlert(t('BEHDASHTIK_AI.COMMON.ERROR'));
  }
};

const saveRole = async role => {
  try {
    await AIApi.put(`roles/${role.role}`, {
      primary_model_id: role.primary_model_id,
      fallback_model_id: role.fallback_model_id,
      traffic_split:
        role.splitModelId && role.splitPct > 0
          ? [{ model_id: role.splitModelId, pct: Number(role.splitPct) }]
          : [],
      params: role.params || {},
      enabled: role.enabled,
    });
    useAlert(t('BEHDASHTIK_AI.COMMON.SAVED'));
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
  <div v-else class="flex flex-col gap-8 pb-8">
    <!-- providers -->
    <section>
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-medium text-n-slate-12">
          {{ t('BEHDASHTIK_AI.MODELS.PROVIDERS') }}
        </h3>
        <button
          class="px-3 py-1.5 text-sm text-white rounded-lg bg-n-brand"
          @click="
            newProvider = {
              name: '',
              base_url: 'https://openrouter.ai/api/v1',
              api_key: '',
              enabled: true,
            }
          "
        >
          {{ t('BEHDASHTIK_AI.COMMON.ADD') }}
        </button>
      </div>
      <div
        v-for="provider in newProvider
          ? [...providers, newProvider]
          : providers"
        :key="provider.id || 'new'"
        class="flex flex-wrap items-center gap-2 p-3 mb-2 border rounded-xl border-n-weak"
      >
        <input
          v-model="provider.name"
          class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
          :placeholder="t('BEHDASHTIK_AI.MODELS.PROVIDER_NAME')"
        />
        <input
          v-model="provider.base_url"
          class="flex-1 min-w-48 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
          :placeholder="t('BEHDASHTIK_AI.MODELS.PROVIDER_URL')"
        />
        <input
          v-model="provider.api_key"
          type="password"
          class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1 text-n-slate-12"
          :placeholder="t('BEHDASHTIK_AI.MODELS.PROVIDER_KEY')"
          :title="t('BEHDASHTIK_AI.MODELS.PROVIDER_KEY_HINT')"
        />
        <span class="text-xs text-n-slate-11">
          {{
            provider.has_key
              ? t('BEHDASHTIK_AI.MODELS.PROVIDER_HAS_KEY')
              : t('BEHDASHTIK_AI.MODELS.PROVIDER_ENV_KEY')
          }}
        </span>
        <label class="flex items-center gap-1 text-xs text-n-slate-11">
          <input v-model="provider.enabled" type="checkbox" />
          {{ t('BEHDASHTIK_AI.COMMON.ENABLED') }}
        </label>
        <button
          class="px-3 py-1 text-sm text-white rounded-lg bg-n-brand"
          @click="saveProvider(provider)"
        >
          {{ t('BEHDASHTIK_AI.COMMON.SAVE') }}
        </button>
        <button
          v-if="provider.id"
          class="px-3 py-1 text-sm border rounded-lg border-n-weak text-n-slate-12"
          @click="testProvider(provider)"
        >
          {{ t('BEHDASHTIK_AI.COMMON.TEST') }}
        </button>
        <span
          v-if="testResult[`p${provider.id}`]"
          class="text-xs text-n-slate-11"
        >
          {{ testResult[`p${provider.id}`] }}
        </span>
      </div>
    </section>

    <!-- models -->
    <section>
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-medium text-n-slate-12">
          {{ t('BEHDASHTIK_AI.MODELS.MODELS') }}
        </h3>
        <button
          class="px-3 py-1.5 text-sm text-white rounded-lg bg-n-brand"
          @click="
            newModel = {
              provider_id: providers[0] && providers[0].id,
              model_name: '',
              label: '',
              enabled: true,
              supports_audio: false,
              input_cost_per_mtok: 0,
              output_cost_per_mtok: 0,
            }
          "
        >
          {{ t('BEHDASHTIK_AI.COMMON.ADD') }}
        </button>
      </div>
      <div class="overflow-x-auto border rounded-xl border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left border-b text-n-slate-11 border-n-weak">
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.MODEL_NAME') }}</th>
              <th class="p-2">
                {{ t('BEHDASHTIK_AI.MODELS.MODEL_PROVIDER') }}
              </th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.MODEL_COST_IN') }}</th>
              <th class="p-2">
                {{ t('BEHDASHTIK_AI.MODELS.MODEL_COST_OUT') }}
              </th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.MODEL_AUDIO') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ENABLED') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ACTIONS') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="model in newModel ? [...models, newModel] : models"
              :key="model.id || 'new'"
              class="border-b border-n-weak text-n-slate-12"
            >
              <td class="p-2">
                <input
                  v-model="model.model_name"
                  class="w-56 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                />
              </td>
              <td class="p-2">
                <select
                  v-model="model.provider_id"
                  class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                >
                  <option
                    v-for="provider in providers"
                    :key="provider.id"
                    :value="provider.id"
                  >
                    {{ provider.name }}
                  </option>
                </select>
              </td>
              <td class="p-2">
                <input
                  v-model.number="model.input_cost_per_mtok"
                  type="number"
                  step="0.01"
                  class="w-20 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                />
              </td>
              <td class="p-2">
                <input
                  v-model.number="model.output_cost_per_mtok"
                  type="number"
                  step="0.01"
                  class="w-20 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                />
              </td>
              <td class="p-2">
                <input v-model="model.supports_audio" type="checkbox" />
              </td>
              <td class="p-2">
                <input v-model="model.enabled" type="checkbox" />
              </td>
              <td class="p-2 whitespace-nowrap">
                <button
                  class="px-2 py-1 text-xs text-white rounded-lg bg-n-brand"
                  @click="saveModel(model)"
                >
                  {{ t('BEHDASHTIK_AI.COMMON.SAVE') }}
                </button>
                <button
                  v-if="model.id"
                  class="px-2 py-1 ml-1 text-xs border rounded-lg border-n-weak"
                  @click="testModel(model)"
                >
                  {{ t('BEHDASHTIK_AI.COMMON.TEST') }}
                </button>
                <button
                  v-if="model.id"
                  class="px-2 py-1 ml-1 text-xs rounded-lg text-n-ruby-11 border border-n-weak"
                  @click="deleteModel(model)"
                >
                  {{ t('BEHDASHTIK_AI.COMMON.DELETE') }}
                </button>
                <span
                  v-if="testResult[`m${model.id}`]"
                  class="ml-1 text-xs text-n-slate-11"
                >
                  {{ testResult[`m${model.id}`] }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- roles -->
    <section>
      <h3 class="text-sm font-medium text-n-slate-12">
        {{ t('BEHDASHTIK_AI.MODELS.ROLES') }}
      </h3>
      <p class="mb-2 text-xs text-n-slate-11">
        {{ t('BEHDASHTIK_AI.MODELS.ROLES_HINT') }}
      </p>
      <div class="overflow-x-auto border rounded-xl border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left border-b text-n-slate-11 border-n-weak">
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.ROLE') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.PRIMARY') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.MODELS.FALLBACK') }}</th>
              <th class="p-2" :title="t('BEHDASHTIK_AI.MODELS.SPLIT_HINT')">
                {{ t('BEHDASHTIK_AI.MODELS.SPLIT') }}
              </th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ENABLED') }}</th>
              <th class="p-2">{{ t('BEHDASHTIK_AI.COMMON.ACTIONS') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="role in roles"
              :key="role.role"
              class="border-b border-n-weak text-n-slate-12"
            >
              <td class="p-2 font-mono text-xs">{{ role.role }}</td>
              <td class="p-2">
                <select
                  v-model="role.primary_model_id"
                  class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                >
                  <option :value="null">
                    {{ t('BEHDASHTIK_AI.MODELS.NONE') }}
                  </option>
                  <option
                    v-for="model in enabledModels"
                    :key="model.id"
                    :value="model.id"
                  >
                    {{ model.model_name }}
                  </option>
                </select>
              </td>
              <td class="p-2">
                <select
                  v-model="role.fallback_model_id"
                  class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                >
                  <option :value="null">
                    {{ t('BEHDASHTIK_AI.MODELS.NONE') }}
                  </option>
                  <option
                    v-for="model in enabledModels"
                    :key="model.id"
                    :value="model.id"
                  >
                    {{ model.model_name }}
                  </option>
                </select>
              </td>
              <td class="p-2 whitespace-nowrap">
                <select
                  v-model="role.splitModelId"
                  class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                  :title="t('BEHDASHTIK_AI.MODELS.SPLIT_MODEL')"
                >
                  <option :value="null">
                    {{ t('BEHDASHTIK_AI.MODELS.NONE') }}
                  </option>
                  <option
                    v-for="model in enabledModels"
                    :key="model.id"
                    :value="model.id"
                  >
                    {{ model.model_name }}
                  </option>
                </select>
                <input
                  v-model.number="role.splitPct"
                  type="number"
                  min="0"
                  max="100"
                  class="w-16 px-2 py-1 ml-1 text-sm border rounded-lg border-n-weak bg-n-surface-1"
                  :title="t('BEHDASHTIK_AI.MODELS.SPLIT_PCT')"
                />
              </td>
              <td class="p-2">
                <input v-model="role.enabled" type="checkbox" />
              </td>
              <td class="p-2">
                <button
                  class="px-2 py-1 text-xs text-white rounded-lg bg-n-brand"
                  @click="saveRole(role)"
                >
                  {{ t('BEHDASHTIK_AI.COMMON.SAVE') }}
                </button>
                <span
                  v-if="role.splitModelId && role.splitPct > 0"
                  class="ml-1 text-xs text-n-slate-11"
                >
                  {{ `${role.splitPct}% → ${modelName(role.splitModelId)}` }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
