export type Role = 'admin' | 'agent' | 'requester';
export type TicketStatus = 'new' | 'open' | 'pending' | 'resolved' | 'closed';
export type TicketPriority = 'low' | 'normal' | 'high' | 'urgent';
export type MessageKind = 'inbound' | 'outbound' | 'note' | 'system';

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  mfa_enabled: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
}

export interface AuthState {
  status: 'authenticated' | 'mfa_required' | 'mfa_enrolment_required';
  user: User | null;
  csrf_token: string | null;
}

export interface TemplateField {
  key: string;
  label: string;
  type: 'text' | 'textarea' | 'select' | 'checkbox' | 'date' | 'number' | 'email';
  required: boolean;
  options: string[];
  help: string;
}

export interface Template {
  id: string;
  slug: string;
  name: string;
  description: string;
  icon: string;
  fields: TemplateField[];
  default_priority: TicketPriority;
  is_fallback: boolean;
  is_active: boolean;
  sort_order: number;
}

export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  is_inline: boolean;
  created_at: string;
}

export interface TicketMessage {
  id: string;
  kind: MessageKind;
  author_name: string;
  author_email: string;
  body_text: string;
  body_html: string | null;
  remote_content_blocked: boolean;
  created_at: string;
  attachments: Attachment[];
}

export interface TicketSummary {
  id: string;
  key: string;
  number: number;
  subject: string;
  status: TicketStatus;
  priority: TicketPriority;
  source: string;
  requester_name: string;
  requester_email: string;
  assignee_name: string | null;
  template_name: string | null;
  created_at: string;
  last_activity_at: string;
  message_count: number;
  attachment_count: number;
}

export interface TicketDetail extends TicketSummary {
  template_id: string | null;
  assignee_id: string | null;
  field_values: Record<string, unknown>;
  template_fields: TemplateField[];
  messages: TicketMessage[];
}

export interface TicketListResponse {
  items: TicketSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface UploadResult {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface MailHealth {
  outbound_configured: boolean;
  inbound_enabled: boolean;
  queued: number;
  failed: number;
  sent_last_24h: number;
  last_inbound_at: string | null;
}

export interface AuditEntry {
  id: string;
  actor_label: string;
  action: string;
  object_type: string;
  object_id: string;
  ip_address: string;
  detail: Record<string, unknown>;
  created_at: string;
}
