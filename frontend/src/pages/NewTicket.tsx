import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Template, UploadResult } from '../api/types';
import { useIsStaff } from '../auth/AuthContext';
import AttachmentPicker from '../components/AttachmentPicker';
import TemplateFields from '../components/TemplateFields';
import { ErrorNote, Spinner } from '../components/ui';

export default function NewTicket() {
  const navigate = useNavigate();
  const isStaff = useIsStaff();

  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [selected, setSelected] = useState<Template | null>(null);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});
  const [attachments, setAttachments] = useState<UploadResult[]>([]);
  const [requesterEmail, setRequesterEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .listTemplates()
      .then(setTemplates)
      .catch((err: Error) => setError(err.message));
  }, []);

  function chooseTemplate(template: Template) {
    setSelected(template);
    // Field answers belong to one template; start clean on a switch.
    setFieldValues({});
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const ticket = await api.createTicket({
        subject,
        body,
        template_id: selected?.id ?? null,
        field_values: fieldValues,
        attachment_ids: attachments.map((a) => a.id),
        ...(isStaff && requesterEmail ? { requester_email: requesterEmail } : {}),
      });
      navigate(`/tickets/${ticket.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the ticket');
      setBusy(false);
    }
  }

  if (!templates) return <Spinner label="Loading templates…" />;

  if (!selected) {
    return (
      <section className="page">
        <header className="page-header">
          <div>
            <h1>New ticket</h1>
            <p className="page-subtitle">Pick the form that fits best.</p>
          </div>
        </header>
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <div className="template-grid">
          {templates.map((template) => (
            <button
              key={template.id}
              type="button"
              className="template-card"
              onClick={() => chooseTemplate(template)}
            >
              <span className="template-name">{template.name}</span>
              <span className="template-description">{template.description}</span>
              {template.is_fallback ? (
                <span className="template-tag">Used for emailed-in tickets</span>
              ) : null}
            </button>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="page page-narrow">
      <header className="page-header">
        <div>
          <h1>{selected.name}</h1>
          <p className="page-subtitle">{selected.description}</p>
        </div>
        <button className="btn btn-link" type="button" onClick={() => setSelected(null)}>
          Change form
        </button>
      </header>

      <form className="card form" onSubmit={handleSubmit}>
        {error ? <ErrorNote>{error}</ErrorNote> : null}

        {isStaff ? (
          <label className="field">
            <span>Raise on behalf of (optional)</span>
            <input
              type="email"
              placeholder="person@example.com"
              value={requesterEmail}
              onChange={(e) => setRequesterEmail(e.target.value)}
            />
            <small className="field-help">
              Leave blank to raise it under your own account. A new requester account is created
              automatically if the address is unknown.
            </small>
          </label>
        ) : null}

        <label className="field">
          <span>
            Subject<em className="required"> *</em>
          </span>
          <input
            type="text"
            required
            maxLength={500}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </label>

        <label className="field">
          <span>
            What is happening?<em className="required"> *</em>
          </span>
          <textarea
            rows={6}
            required
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>

        <TemplateFields
          fields={selected.fields}
          values={fieldValues}
          onChange={setFieldValues}
          disabled={busy}
        />

        <AttachmentPicker files={attachments} onChange={setAttachments} disabled={busy} />

        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create ticket'}
          </button>
        </div>
      </form>
    </section>
  );
}
