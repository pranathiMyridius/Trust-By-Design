// Where the backend API lives. Local development keeps the old default
// (uvicorn on 127.0.0.1:8000). A production build sets VITE_API_BASE_URL
// at build time: "" when the backend serves this frontend itself (same
// origin, so requests go to /api/...), or a full https:// URL when the
// API is hosted separately.
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
