import { useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorNote } from '../components/ui';

export default function MfaChallenge() {
  const { submitMfaCode, signOut, error } = useAuth();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await submitMfaCode(code.trim());
    } catch {
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">Two-factor code</h1>
        <p className="auth-subtitle">
          Enter the six-digit code from your authenticator app, or one of your recovery codes.
        </p>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <label className="field">
          <span>Code</span>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
          />
        </label>

        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Checking…' : 'Verify'}
        </button>
        <button className="btn btn-link" type="button" onClick={() => void signOut()}>
          Cancel and sign out
        </button>
      </form>
    </div>
  );
}
