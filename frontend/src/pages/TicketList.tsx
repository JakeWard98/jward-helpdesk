import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { TicketListResponse } from '../api/types';
import { useIsStaff } from '../auth/AuthContext';
import {
  EmptyState,
  ErrorNote,
  PriorityBadge,
  Spinner,
  StatusBadge,
  formatWhen,
} from '../components/ui';

interface ViewTab {
  key: string;
  label: string;
  staffOnly?: boolean;
}

const VIEWS: ViewTab[] = [
  { key: 'open', label: 'Open' },
  { key: 'mine', label: 'Assigned to me', staffOnly: true },
  { key: 'unassigned', label: 'Unassigned', staffOnly: true },
  { key: 'all', label: 'All' },
];

export default function TicketList() {
  const isStaff = useIsStaff();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<TicketListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const view = params.get('view') ?? 'open';
  const query = params.get('q') ?? '';
  const page = Number(params.get('page') ?? '1');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.listTickets({ view, q: query, page, page_size: 25 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load tickets');
    } finally {
      setLoading(false);
    }
  }, [view, query, page]);

  useEffect(() => {
    void load();
  }, [load]);

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(params);
    Object.entries(next).forEach(([key, value]) => {
      if (value) merged.set(key, value);
      else merged.delete(key);
    });
    // Any filter change starts from the first page again.
    if (!('page' in next)) merged.delete('page');
    setParams(merged);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Tickets</h1>
          <p className="page-subtitle">
            {data ? `${data.total} ticket${data.total === 1 ? '' : 's'}` : 'Loading…'}
          </p>
        </div>
        <Link className="btn btn-primary" to="/tickets/new">
          New ticket
        </Link>
      </header>

      <div className="toolbar">
        <div className="tabs" role="tablist">
          {VIEWS.filter((v) => isStaff || !v.staffOnly).map((v) => (
            <button
              key={v.key}
              role="tab"
              aria-selected={view === v.key}
              className={`tab ${view === v.key ? 'tab-active' : ''}`}
              onClick={() => update({ view: v.key })}
            >
              {v.label}
            </button>
          ))}
        </div>
        <input
          className="search"
          type="search"
          placeholder="Search subject or TKT-number"
          defaultValue={query}
          onKeyDown={(e) => {
            if (e.key === 'Enter') update({ q: (e.target as HTMLInputElement).value });
          }}
        />
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {loading ? <Spinner label="Loading tickets…" /> : null}

      {!loading && data && data.items.length === 0 ? (
        <EmptyState
          title="Nothing here"
          hint={
            view === 'open'
              ? 'No open tickets. Try the All tab, or raise a new one.'
              : 'No tickets match this view.'
          }
        />
      ) : null}

      {!loading && data && data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="ticket-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Subject</th>
                <th>Status</th>
                <th>Priority</th>
                {isStaff ? <th>Requester</th> : null}
                {isStaff ? <th>Assignee</th> : null}
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((ticket) => (
                <tr key={ticket.id}>
                  <td className="mono">
                    <Link to={`/tickets/${ticket.id}`}>{ticket.key}</Link>
                  </td>
                  <td>
                    <Link className="subject-link" to={`/tickets/${ticket.id}`}>
                      {ticket.subject}
                    </Link>
                    <div className="row-meta">
                      {ticket.template_name ?? 'No template'}
                      {ticket.source === 'email' ? ' · by email' : ''}
                      {ticket.attachment_count > 0
                        ? ` · ${ticket.attachment_count} attachment${
                            ticket.attachment_count === 1 ? '' : 's'
                          }`
                        : ''}
                    </div>
                  </td>
                  <td>
                    <StatusBadge status={ticket.status} />
                  </td>
                  <td>
                    <PriorityBadge priority={ticket.priority} />
                  </td>
                  {isStaff ? <td className="truncate">{ticket.requester_name}</td> : null}
                  {isStaff ? (
                    <td className="truncate">{ticket.assignee_name ?? '—'}</td>
                  ) : null}
                  <td title={ticket.last_activity_at}>{formatWhen(ticket.last_activity_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {totalPages > 1 ? (
        <nav className="pagination">
          <button
            className="btn btn-secondary"
            disabled={page <= 1}
            onClick={() => update({ page: String(page - 1) })}
          >
            Previous
          </button>
          <span>
            Page {page} of {totalPages}
          </span>
          <button
            className="btn btn-secondary"
            disabled={page >= totalPages}
            onClick={() => update({ page: String(page + 1) })}
          >
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}
