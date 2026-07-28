/* global axios */
// Thin client for the Behdashtik AI service, proxied through Rails at
// /api/v1/accounts/:accountId/behdashtik_ai/* (see BehdashtikAi::ProxyController).

const baseUrl = () => {
  const accountId = window.location.pathname.split('/')[3];
  return `/api/v1/accounts/${accountId}/behdashtik_ai`;
};

export default {
  get: (path, params = {}) => axios.get(`${baseUrl()}/${path}`, { params }),
  post: (path, data = {}) => axios.post(`${baseUrl()}/${path}`, data),
  patch: (path, data = {}) => axios.patch(`${baseUrl()}/${path}`, data),
  put: (path, data = {}) => axios.put(`${baseUrl()}/${path}`, data),
  delete: path => axios.delete(`${baseUrl()}/${path}`),
};
