import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ErrorNote, InfoNote } from '../components/ui';

export default function ChangePassword() {
  const { refresh, signOut } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setError('The two new passwords do not match');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the password');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">Choose a new password</h1>
        <InfoNote>
          At least 12 characters. Either mix upper case, lower case, digits and symbols, or use a
          passphrase of 20 characters or more.
        </InfoNote>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

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
        <label className="field">
          <span>Repeat new password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>

        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Save and continue'}
        </button>
        <button className="btn btn-link" type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </form>
    </div>
  );
}
