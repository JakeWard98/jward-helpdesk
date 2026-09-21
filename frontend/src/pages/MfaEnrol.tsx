import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ErrorNote, InfoNote, Spinner } from '../components/ui';

/**
 * First-run MFA enrolment. The QR image is an SVG produced by our own backend,
 * so the TOTP secret never travels to a third-party chart service.
 */
export default function MfaEnrol() {
  const { finishEnrolment, signOut } = useAuth();
  const [qr, setQr] = useState<string | null>(null);
  const [secret, setSecret] = useState('');
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .startMfaEnrolment()
      .then((result) => {
        if (cancelled) return;
        setQr(result.qr_svg);
        setSecret(result.secret);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleConfirm(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.confirmMfaEnrolment(code.trim());
      setRecoveryCodes(result.recovery_codes);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not confirm the code');
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  if (recoveryCodes) {
    return (
      <div className="auth-shell">
        <div className="auth-card auth-card-wide">
          <h1 className="auth-title">Save your recovery codes</h1>
          <InfoNote>
            Each code works once, and this is the only time they are shown. Keep them somewhere
            you can reach without this helpdesk.
          </InfoNote>
          <ul className="recovery-codes">
            {recoveryCodes.map((rc) => (
              <li key={rc}>{rc}</li>
            ))}
          </ul>
          <button
            className="btn btn-secondary"
            type="button"
            onClick={() => navigator.clipboard?.writeText(recoveryCodes.join('\n'))}
          >
            Copy to clipboard
          </button>
          <button className="btn btn-primary" type="button" onClick={() => void finishEnrolment()}>
            I have saved them - continue
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <form className="auth-card auth-card-wide" onSubmit={handleConfirm}>
        <h1 className="auth-title">Set up two-factor authentication</h1>
        <p className="auth-subtitle">
          This account needs a second factor. Scan the code with an authenticator app, then enter
          the six digits it shows.
        </p>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        {qr ? (
          // Server-generated SVG from our own origin, rendered under a CSP
          // that blocks script regardless.
          <div className="qr" dangerouslySetInnerHTML={{ __html: qr }} />
        ) : (
          <Spinner label="Generating your code…" />
        )}

        <details className="manual-secret">
          <summary>Can't scan it?</summary>
          <p>Enter this key manually:</p>
          <code>{secret}</code>
        </details>

        <label className="field">
          <span>Code from your app</span>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
          />
        </label>

        <button className="btn btn-primary" type="submit" disabled={busy || !qr}>
          {busy ? 'Confirming…' : 'Confirm and continue'}
        </button>
        <button className="btn btn-link" type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </form>
    </div>
  );
}
