import type { TicketMessage } from '../api/types';
import { api } from '../api/client';
import { formatBytes, formatExact, formatWhen } from './ui';

const KIND_LABEL: Record<TicketMessage['kind'], string> = {
  inbound: 'From requester',
  outbound: 'Reply sent by email',
  note: 'Internal note',
  system: 'Automatic email',
};

export default function MessageTimeline({ messages }: { messages: TicketMessage[] }) {
  return (
    <ol className="timeline">
      {messages.map((message) => (
        <li key={message.id} className={`message message-${message.kind}`}>
          <header className="message-header">
            <span className="message-author">{message.author_name || message.author_email}</span>
            <span className="message-kind">{KIND_LABEL[message.kind]}</span>
            <time className="message-time" title={formatExact(message.created_at)}>
              {formatWhen(message.created_at)}
            </time>
          </header>

          {message.body_html ? (
            // The HTML was sanitised server-side with nh3 (scripts, styles,
            // handlers and remote images removed) and is rendered under a CSP
            // that blocks script execution in any case.
            <div
              className="message-body message-body-html"
              dangerouslySetInnerHTML={{ __html: message.body_html }}
            />
          ) : (
            <div className="message-body">{message.body_text}</div>
          )}

          {message.remote_content_blocked ? (
            <p className="message-blocked">
              Images hosted elsewhere were removed from this email.
            </p>
          ) : null}

          {message.attachments.filter((a) => !a.is_inline).length > 0 ? (
            <ul className="message-attachments">
              {message.attachments
                .filter((a) => !a.is_inline)
                .map((attachment) => (
                  <li key={attachment.id}>
                    <a href={api.attachmentUrl(attachment.id)} download={attachment.filename}>
                      {attachment.filename}
                    </a>
                    <span className="chip-size">{formatBytes(attachment.size_bytes)}</span>
                  </li>
                ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
