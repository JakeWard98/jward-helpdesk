import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import type { AuditEntry, MailHealth, Template, User } from '../api/types';
import { ErrorNote, InfoNote, Spinner, formatExact, formatWhen } from '../components/ui';

type Tab = 'templates' | 'users' | 'mail' | 'audit';

export default function Admin() {
  const [tab, setTab] = useState<Tab>('templates');

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Administration</h1>
          <p className="page-subtitle">Forms, accounts, mail plumbing and the audit trail.</p>
        </div>
      </header>

      <div className="tabs" role="tablist">
        {(
          [
            ['templates', 'Templates'],
            ['users', 'Users'],
            ['mail', 'Mail'],
            ['audit', 'Audit log'],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            className={`tab ${tab === key ? 'tab-active' : ''}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'templates' ? <TemplatesPanel /> : null}
      {tab === 'users' ? <UsersPanel /> : null}
      {tab === 'mail' ? <MailPanel /> : null}
      {tab === 'audit' ? <AuditPanel /> : null}
    </section>
  );
}

function TemplatesPanel() {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    try {
      setTemplates(await api.listTemplates(true));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load templates');
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function makeFallback(template: Template) {
    try {
      await api.updateTemplate(template.id, { ...template, is_fallback: true });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the template');
    }
  }

  if (!templates) return <Spinner label="Loading templates…" />;

  return (
    <div className="panel">
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <InfoNote>
        The fallback form is what a ticket created from an inbound email starts on. Change a
        ticket's form later from its detail page.
      </InfoNote>

      <div className="table-wrap">
        <table className="ticket-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Slug</th>
              <th>Fields</th>
              <th>Default priority</th>
              <th>Fallback</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {templates.map((template) => (
              <tr key={template.id} className={template.is_active ? '' : 'row-muted'}>
                <td>
                  {template.name}
                  {!template.is_active ? <span className="row-meta">retired</span> : null}
                </td>
                <td className="mono">{template.slug}</td>
                <td>{template.fields.length}</td>
                <td>{template.default_priority}</td>
                <td>{template.is_fallback ? 'Yes' : ''}</td>
                <td>
                  {!template.is_fallback && template.is_active ? (
                    <button
                      className="btn btn-secondary btn-small"
                      onClick={() => void makeFallback(template)}
                    >
                      Use for email
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UsersPanel() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('agent');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  async function reload() {
    try {
      setUsers(await api.listUsers());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load users');
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createUser({ email, role, password: password || null });
      setEmail('');
      setPassword('');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the account');
    } finally {
      setBusy(false);
    }
  }

  async function update(user: User, body: Record<string, unknown>) {
    try {
      await api.updateUser(user.id, body);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the account');
    }
  }

  if (!users) return <Spinner label="Loading users…" />;

  return (
    <div className="panel">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <form className="card form form-inline" onSubmit={createUser}>
        <h2 className="card-title">Add an account</h2>
        <label className="field">
          <span>Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="requester">Requester</option>
            <option value="agent">Agent</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label className="field">
          <span>Temporary password</span>
          <input
            type="password"
            minLength={12}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <small className="field-help">
            They must change it at first sign-in. Leave blank for an email-only requester.
          </small>
        </label>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create'}
        </button>
      </form>

      <div className="table-wrap">
        <table className="ticket-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>MFA</th>
              <th>Active</th>
              <th>Last sign-in</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className={user.is_active ? '' : 'row-muted'}>
                <td className="truncate">{user.email}</td>
                <td>{user.role}</td>
                <td>{user.mfa_enabled ? 'On' : '—'}</td>
                <td>{user.is_active ? 'Yes' : 'No'}</td>
                <td>{user.last_login_at ? formatWhen(user.last_login_at) : 'never'}</td>
                <td className="row-buttons">
                  {user.mfa_enabled ? (
                    <button
                      className="btn btn-secondary btn-small"
                      title="Use when someone has lost their authenticator. They enrol again at next sign-in."
                      onClick={() => void update(user, { clear_mfa: true })}
                    >
                      Reset MFA
                    </button>
                  ) : null}
                  <button
                    className="btn btn-secondary btn-small"
                    onClick={() => void update(user, { is_active: !user.is_active })}
                  >
                    {user.is_active ? 'Disable' : 'Enable'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MailPanel() {
  const [health, setHealth] = useState<MailHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .mailHealth()
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!health) return <Spinner label="Checking mail…" />;

  return (
    <div className="panel">
      <div className="stat-grid">
        <Stat label="Outbound SMTP" value={health.outbound_configured ? 'Configured' : 'Off'} />
        <Stat label="Inbound IMAP" value={health.inbound_enabled ? 'Polling' : 'Off'} />
        <Stat label="Queued" value={String(health.queued)} />
        <Stat label="Failed" value={String(health.failed)} warn={health.failed > 0} />
        <Stat label="Sent (24h)" value={String(health.sent_last_24h)} />
        <Stat
          label="Last inbound"
          value={health.last_inbound_at ? formatWhen(health.last_inbound_at) : 'never'}
        />
      </div>
      {health.failed > 0 ? (
        <ErrorNote>
          Some emails could not be delivered after several attempts. Check the worker logs:
          <code> docker logs helpdesk-worker</code>
        </ErrorNote>
      ) : null}
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={`stat ${warn ? 'stat-warn' : ''}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

function AuditPanel() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listAudit(150)
      .then(setEntries)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!entries) return <Spinner label="Loading audit log…" />;

  return (
    <div className="panel table-wrap">
      <table className="ticket-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Actor</th>
            <th>Object</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id}>
              <td title={formatExact(entry.created_at)}>{formatWhen(entry.created_at)}</td>
              <td className="mono">{entry.action}</td>
              <td className="truncate">{entry.actor_label}</td>
              <td className="truncate">
                {entry.object_type}
                {entry.object_id ? ` ${entry.object_id.slice(0, 8)}` : ''}
              </td>
              <td className="mono">{entry.ip_address || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
