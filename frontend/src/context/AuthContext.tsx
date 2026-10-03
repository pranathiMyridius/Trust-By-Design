import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  login as apiLogin,
  logout as apiLogout,
  getCurrentUser,
  type CurrentUser,
} from "../api/auth";
import { getAuthToken } from "../api/http";

interface AuthContextValue {
  user: CurrentUser | null;
  loading: boolean;
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // A token may already be in localStorage from a previous session --
    // resolve it to a real user before rendering the app, rather than
    // flashing the login page first.
    if (!getAuthToken()) {
      setLoading(false);
      return;
    }

    getCurrentUser()
      .then((current) => setUser(current))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const current = await apiLogin(email, password);
      setUser(current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      throw err;
    }
  }, []);

  const logout = useCallback(() => {
    apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, error, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
