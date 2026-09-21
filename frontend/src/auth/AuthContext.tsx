import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, setCsrfToken } from '../api/client';
import type { AuthState, User } from '../api/types';

type Stage = 'loading' | 'signed-out' | 'mfa_required' | 'mfa_enrolment_required' | 'ready';

interface AuthContextValue {
  stage: Stage;
  user: User | null;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  submitMfaCode: (code: string) => Promise<void>;
  finishEnrolment: (user?: User) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function stageFor(state: AuthState): Stage {
  if (state.status === 'authenticated') return 'ready';
  return state.status;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [stage, setStage] = useState<Stage>('loading');
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback((state: AuthState) => {
    setCsrfToken(state.csrf_token);
    setUser(state.user);
    setStage(stageFor(state));
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.me());
    } catch {
      // A 401 here simply means there is no live session.
      setCsrfToken(null);
      setUser(null);
      setStage('signed-out');
    }
  }, [apply]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      setError(null);
      try {
        apply(await api.login(email, password));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Sign-in failed');
        throw err;
      }
    },
    [apply],
  );

  const submitMfaCode = useCallback(
    async (code: string) => {
      setError(null);
      try {
        apply(await api.verifyMfa(code));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Verification failed');
        throw err;
      }
    },
    [apply],
  );

  const finishEnrolment = useCallback(async () => {
    // Confirming enrolment rotates the session token, so re-read /auth/me to
    // pick up the new CSRF token rather than reusing the old one.
    await refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setCsrfToken(null);
      setUser(null);
      setStage('signed-out');
    }
  }, []);

  const value = useMemo(
    () => ({ stage, user, error, signIn, submitMfaCode, finishEnrolment, signOut, refresh }),
    [stage, user, error, signIn, submitMfaCode, finishEnrolment, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}

export function useIsStaff(): boolean {
  const { user } = useAuth();
  return user?.role === 'admin' || user?.role === 'agent';
}
