/* global axios */
// Thin client for the Behdashtik AI services, proxied through Rails at
// /api/v1/accounts/:accountId/behdashtik_ai/* (see BehdashtikAi::ProxyController).
// Two agents are managed from the same panel — `service` picks which one.

import { ref } from 'vue';

export const SERVICES = ['site', 'basalam'];

export const service = ref('site');

const baseUrl = () => {
  const accountId = window.location.pathname.split('/')[3];
  return `/api/v1/accounts/${accountId}/behdashtik_ai`;
};

const client = resolve => {
  const config = (params = {}) => ({
    params: { ...params, service: resolve() },
  });
  return {
    get: (path, params = {}) =>
      axios.get(`${baseUrl()}/${path}`, config(params)),
    post: (path, data = {}) =>
      axios.post(`${baseUrl()}/${path}`, data, config()),
    patch: (path, data = {}) =>
      axios.patch(`${baseUrl()}/${path}`, data, config()),
    put: (path, data = {}) => axios.put(`${baseUrl()}/${path}`, data, config()),
    delete: path => axios.delete(`${baseUrl()}/${path}`, config()),
  };
};

// pinned to one agent, for screens that talk to both at once
export const forService = name => client(() => name);

export default client(() => service.value);
