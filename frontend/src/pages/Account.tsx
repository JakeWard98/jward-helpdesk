import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ErrorNote, InfoNote } from '../components/ui';

export default function Account() {
  const { user, signOut } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    try {
      await api.changePassword(current, next);
      setCurrent('');
      setNext('');
      setMessage('Password changed. Your other sessions were signed out.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the password');
    }
  }

  async function regenerate() {
    setError(null);
    try {
      const result = await api.regenerateRecoveryCodes();
      setCodes(result.recovery_codes);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not regenerate codes');
    }
  }

  return (
    <section className="page page-narrow">
      <header className="page-header">
        <div>
          <h1>Your account</h1>
          <p className="page-subtitle">
            {user?.email} · {user?.role} · MFA {user?.mfa_enabled ? 'enabled' : 'not set up'}
          </p>
        </div>
      </header>

      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {message ? <InfoNote>{message}</InfoNote> : null}

      <form className="card form" onSubmit={changePassword}>
        <h2 className="card-title">Change password</h2>
        <label className="field">
          <span>Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <button className="btn btn-primary" type="submit">
          Change password
        </button>
      </form>

      {user?.mfa_enabled ? (
        <div className="card">
          <h2 className="card-title">Recovery codes</h2>
          <p className="reply-hint">
            Generating a new set immediately invalidates the old one.
          </p>
          {codes ? (
            <ul className="recovery-codes">
              {codes.map((code) => (
                <li key={code}>{code}</li>
              ))}
            </ul>
          ) : null}
          <button className="btn btn-secondary" type="button" onClick={() => void regenerate()}>
            Generate new recovery codes
          </button>
        </div>
      ) : null}

      <div className="card">
        <h2 className="card-title">Sessions</h2>
        <p className="reply-hint">
          Signs you out on every device, including this one. Use it if you think a session was
          taken.
        </p>
        <button
          className="btn btn-secondary"
          type="button"
          onClick={async () => {
            await api.logoutEverywhere().catch(() => undefined);
            await signOut();
          }}
        >
          Sign out everywhere
        </button>
      </div>
    </section>
  );
}
