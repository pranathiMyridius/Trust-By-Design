// Shared auth plumbing for every API module. Kept separate from
// AuthContext.tsx (a React context) so plain fetch helpers like
// assessments.ts can read the current token without importing React.
const TOKEN_KEY = "auth-token";

export function getAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token: string | null): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (token) {
      window.localStorage.setItem(TOKEN_KEY, token);
    } else {
      window.localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    // Ignore storage failures (e.g. private browsing) -- the session
    // just won't persist across a reload.
  }
}

// Drop-in replacement for `fetch` that attaches the current bearer token,
// if any, to every request's Authorization header without disturbing any
// headers the caller already set.
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  const token = getAuthToken();

  const headers = new Headers(init.headers);

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  return fetch(input, { ...init, headers });
}
