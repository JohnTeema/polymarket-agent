import axios from "axios";

const BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE)
  || (typeof window !== 'undefined' && window.__VITE_API_BASE__)
  || "/api";

const api = axios.create({
  baseURL: BASE,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.response.use(
  r => r,
  e => {
    console.error("[API ERROR]", e.message, e.response?.data);
    return Promise.reject(e);
  }
);

export default api;
