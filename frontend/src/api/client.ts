import type {
  AuditEntry,
  AuthState,
  MailHealth,
  Template,
  TicketDetail,
  TicketListResponse,
  UploadResult,
  User,
} from './types';

/**
 * Thin fetch wrapper.
 *
 * Authentication is a HttpOnly cookie the browser sends automatically, so no
 * token is ever kept in localStorage where a script injection could read it.
 * The CSRF token is not secret - it is held in memory and echoed back in a
 * header, which is what proves a request came from this app rather than from
 * another site that happens to have the cookie.
 */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = {};

  if (method !== 'GET' && method !== 'HEAD') {
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    if (!options.formData) headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: options.formData ?? (options.body ? JSON.stringify(options.body) : undefined),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail =
      (payload && typeof payload.detail === 'string' && payload.detail) ||
      `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export const api = {
  // ---- auth ----
  me: () => request<AuthState>('/auth/me'),
  login: (email: string, password: string) =>
    request<AuthState>('/auth/login', { method: 'POST', body: { email, password } }),
  verifyMfa: (code: string) =>
    request<AuthState>('/auth/mfa/verify', { method: 'POST', body: { code } }),
  startMfaEnrolment: () =>
    request<{ secret: string; otpauth_uri: string; qr_svg: string }>('/auth/mfa/enrol/start', {
      method: 'POST',
    }),
  confirmMfaEnrolment: (code: string) =>
    request<{ recovery_codes: string[] }>('/auth/mfa/enrol/confirm', {
      method: 'POST',
      body: { code },
    }),
  regenerateRecoveryCodes: () =>
    request<{ recovery_codes: string[] }>('/auth/mfa/recovery-codes', { method: 'POST' }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ status: string }>('/auth/password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
  logoutEverywhere: () => request<{ revoked: number }>('/auth/logout-everywhere', { method: 'POST' }),

  // ---- tickets ----
  listTickets: (params: Record<string, string | number | undefined>) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') search.set(key, String(value));
    });
    return request<TicketListResponse>(`/tickets?${search.toString()}`);
  },
  getTicket: (id: string) => request<TicketDetail>(`/tickets/${id}`),
  createTicket: (body: Record<string, unknown>) =>
    request<TicketDetail>('/tickets', { method: 'POST', body }),
  replyToTicket: (id: string, body: Record<string, unknown>) =>
    request<TicketDetail>(`/tickets/${id}/reply`, { method: 'POST', body }),
  updateTicket: (id: string, body: Record<string, unknown>) =>
    request<TicketDetail>(`/tickets/${id}`, { method: 'PATCH', body }),

  // ---- templates ----
  listTemplates: (includeInactive = false) =>
    request<Template[]>(`/templates?include_inactive=${includeInactive}`),
  createTemplate: (body: Record<string, unknown>) =>
    request<Template>('/templates', { method: 'POST', body }),
  updateTemplate: (id: string, body: Record<string, unknown>) =>
    request<Template>(`/templates/${id}`, { method: 'PUT', body }),
  retireTemplate: (id: string) => request<void>(`/templates/${id}`, { method: 'DELETE' }),

  // ---- attachments ----
  upload: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return request<UploadResult>('/uploads', { method: 'POST', formData });
  },
  attachmentUrl: (id: string) => `/api/attachments/${id}`,

  // ---- admin ----
  listUsers: (staffOnly = false) => request<User[]>(`/users?staff_only=${staffOnly}`),
  createUser: (body: Record<string, unknown>) =>
    request<User>('/users', { method: 'POST', body }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request<User>(`/users/${id}`, { method: 'PATCH', body }),
  listAudit: (limit = 100) => request<AuditEntry[]>(`/audit?limit=${limit}`),
  mailHealth: () => request<MailHealth>('/mail/health'),
};
