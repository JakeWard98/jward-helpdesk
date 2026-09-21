import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { Template, TicketDetail as Ticket, UploadResult, User } from '../api/types';
import { useIsStaff } from '../auth/AuthContext';
import AttachmentPicker from '../components/AttachmentPicker';
import MessageTimeline from '../components/MessageTimeline';
import TemplateFields from '../components/TemplateFields';
import {
  ErrorNote,
  InfoNote,
  PriorityBadge,
  Spinner,
  StatusBadge,
  formatExact,
} from '../components/ui';

const STATUSES = ['new', 'open', 'pending', 'resolved', 'closed'] as const;
const PRIORITIES = ['low', 'normal', 'high', 'urgent'] as const;

export default function TicketDetail() {
  const { ticketId = '' } = useParams();
  const isStaff = useIsStaff();

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [agents, setAgents] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [reply, setReply] = useState('');
  const [internal, setInternal] = useState(false);
  const [closeAfter, setCloseAfter] = useState(false);
  const [attachments, setAttachments] = useState<UploadResult[]>([]);
  const [sending, setSending] = useState(false);

  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});
  const [savingFields, setSavingFields] = useState(false);
  const [fieldsSaved, setFieldsSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getTicket(ticketId);
      setTicket(result);
      setFieldValues(result.field_values ?? {});
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the ticket');
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!isStaff) return;
    api.listTemplates().then(setTemplates).catch(() => undefined);
    api.listUsers(true).then(setAgents).catch(() => undefined);
  }, [isStaff]);

  async function patch(body: Record<string, unknown>) {
    try {
      const updated = await api.updateTicket(ticketId, body);
      setTicket(updated);
      setFieldValues(updated.field_values ?? {});
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the ticket');
    }
  }

  async function handleReply(event: FormEvent) {
    event.preventDefault();
    if (!reply.trim()) return;
    setSending(true);
    setError(null);
    try {
      const updated = await api.replyToTicket(ticketId, {
        body: reply,
        internal,
        attachment_ids: attachments.map((a) => a.id),
        ...(isStaff && closeAfter ? { set_status: 'resolved' } : {}),
      });
      setTicket(updated);
      setReply('');
      setAttachments([]);
      setInternal(false);
      setCloseAfter(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the reply');
    } finally {
      setSending(false);
    }
  }

  async function saveFields() {
    setSavingFields(true);
    setFieldsSaved(false);
    await patch({ field_values: fieldValues });
    setSavingFields(false);
    setFieldsSaved(true);
  }

  if (loading && !ticket) return <Spinner label="Loading ticket…" />;
  if (!ticket) return <ErrorNote>{error ?? 'Ticket not found'}</ErrorNote>;

  return (
    <section className="ticket-layout">
      <div className="ticket-main">
        <header className="page-header">
          <div>
            <p className="ticket-key mono">{ticket.key}</p>
            <h1>{ticket.subject}</h1>
            <p className="page-subtitle">
              Raised by {ticket.requester_name} ({ticket.requester_email}) ·{' '}
              {ticket.source === 'email' ? 'by email' : 'in the portal'} ·{' '}
              {formatExact(ticket.created_at)}
            </p>
          </div>
          <div className="badge-row">
            <StatusBadge status={ticket.status} />
            <PriorityBadge priority={ticket.priority} />
          </div>
        </header>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <MessageTimeline messages={ticket.messages} />

        {ticket.status === 'closed' && !isStaff ? (
          <InfoNote>
            This ticket is closed. Replying will reopen it, as will answering the email thread.
          </InfoNote>
        ) : null}

        <form className="card reply-box" onSubmit={handleReply}>
          <h2 className="card-title">
            {internal ? 'Internal note' : isStaff ? 'Reply to requester' : 'Add a reply'}
          </h2>
          {isStaff && !internal ? (
            <p className="reply-hint">
              This is emailed to {ticket.requester_email}. Their answer lands back on this ticket.
            </p>
          ) : null}
          {internal ? (
            <p className="reply-hint">
              Only agents see internal notes. Nothing is emailed.
            </p>
          ) : null}

          <textarea
            rows={6}
            required
            placeholder={internal ? 'Notes for other agents…' : 'Type your message…'}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            disabled={sending}
          />

          <AttachmentPicker files={attachments} onChange={setAttachments} disabled={sending} />

          <div className="reply-actions">
            {isStaff ? (
              <>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={internal}
                    onChange={(e) => setInternal(e.target.checked)}
                  />
                  Internal note
                </label>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={closeAfter}
                    onChange={(e) => setCloseAfter(e.target.checked)}
                  />
                  Mark resolved
                </label>
              </>
            ) : null}
            <button className="btn btn-primary" type="submit" disabled={sending}>
              {sending ? 'Sending…' : internal ? 'Save note' : 'Send reply'}
            </button>
          </div>
        </form>
      </div>

      <aside className="ticket-side">
        <div className="card">
          <h2 className="card-title">Details</h2>

          {isStaff ? (
            <>
              <label className="field">
                <span>Status</span>
                <select
                  value={ticket.status}
                  onChange={(e) => void patch({ status: e.target.value })}
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Priority</span>
                <select
                  value={ticket.priority}
                  onChange={(e) => void patch({ priority: e.target.value })}
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Assignee</span>
                <select
                  value={ticket.assignee_id ?? ''}
                  onChange={(e) => void patch({ assignee_id: e.target.value })}
                >
                  <option value="">Unassigned</option>
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.display_name || agent.email}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Template</span>
                <select
                  value={ticket.template_id ?? ''}
                  onChange={(e) => void patch({ template_id: e.target.value })}
                >
                  <option value="">None</option>
                  {templates.map((template) => (
                    <option key={template.id} value={template.id}>
                      {template.name}
                    </option>
                  ))}
                </select>
                <small className="field-help">
                  Emailed-in tickets start on the fallback form. Switch it here, then fill in the
                  fields below.
                </small>
              </label>
            </>
          ) : (
            <dl className="detail-list">
              <dt>Status</dt>
              <dd>{ticket.status}</dd>
              <dt>Priority</dt>
              <dd>{ticket.priority}</dd>
              <dt>Form</dt>
              <dd>{ticket.template_name ?? '—'}</dd>
            </dl>
          )}
        </div>

        {ticket.template_fields.length > 0 ? (
          <div className="card">
            <h2 className="card-title">{ticket.template_name}</h2>
            {isStaff ? (
              <>
                <TemplateFields
                  fields={ticket.template_fields}
                  values={fieldValues}
                  onChange={(next) => {
                    setFieldValues(next);
                    setFieldsSaved(false);
                  }}
                  disabled={savingFields}
                />
                <button
                  className="btn btn-secondary btn-small"
                  type="button"
                  onClick={() => void saveFields()}
                  disabled={savingFields}
                >
                  {savingFields ? 'Saving…' : fieldsSaved ? 'Saved' : 'Save fields'}
                </button>
              </>
            ) : (
              <dl className="detail-list">
                {ticket.template_fields.map((field) => (
                  <div key={field.key}>
                    <dt>{field.label}</dt>
                    <dd>{formatValue(ticket.field_values[field.key])}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        ) : null}
      </aside>
    </section>
  );
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}
