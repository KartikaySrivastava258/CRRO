import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "/api";

export const api = axios.create({ baseURL, timeout: 20000 });

export const endpoints = {
  health: () => api.get("/health"),
  overview: (days = 14) => api.get("/dashboard/overview", { params: { days } }),
  optimizer: (limit = 15) => api.get("/dashboard/optimizer", { params: { limit } }),
  cases: (limit = 20, offset = 0) => api.get("/cases", { params: { limit, offset } }),
  caseDetail: (caseId) => api.get(`/cases/${encodeURIComponent(caseId)}`),
  caseJourney: (caseId) => api.get(`/cases/${encodeURIComponent(caseId)}/journey`),
  caseExplainability: (caseId) => api.get(`/cases/${encodeURIComponent(caseId)}/explainability`),
  scoreCase: (caseId) => api.post("/recovery/score", null, { params: { case_id: caseId } }),
  whatIf: (caseId) => api.get("/recovery/whatif", { params: { case_id: caseId } }),
  pendingApprovals: (limit = 50) => api.get("/approvals/pending", { params: { limit } }),
  approve: (decisionId, note) => api.post(`/approvals/${decisionId}/approve`, { note }),
  reject: (decisionId, note) => api.post(`/approvals/${decisionId}/reject`, { note }),
  audit: (params = {}) => api.get("/recovery/audit", { params }),
  submitFailedPayment: (payload) => api.post("/events/payment-failed", payload),
  metrics: () => api.get("/metrics"),
};
