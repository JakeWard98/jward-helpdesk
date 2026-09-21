import { useRef, useState } from 'react';
import { api } from '../api/client';
import type { UploadResult } from '../api/types';
import { formatBytes } from './ui';

interface Props {
  files: UploadResult[];
  onChange: (files: UploadResult[]) => void;
  disabled?: boolean;
}

/**
 * Uploads each file immediately and hands back its id. The ticket or reply is
 * submitted with those ids, so the outbound email is never assembled before
 * its attachments exist.
 */
export default function AttachmentPicker({ files, onChange, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFiles(selected: FileList | null) {
    if (!selected?.length) return;
    setBusy(true);
    setError(null);
    const uploaded: UploadResult[] = [];
    for (const file of Array.from(selected)) {
      try {
        uploaded.push(await api.upload(file));
      } catch (err) {
        setError(
          `${file.name}: ${err instanceof Error ? err.message : 'could not be uploaded'}`,
        );
      }
    }
    onChange([...files, ...uploaded]);
    setBusy(false);
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <div className="attachment-picker">
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        disabled={disabled || busy}
        onChange={(e) => void handleFiles(e.target.files)}
      />
      <button
        type="button"
        className="btn btn-secondary btn-small"
        disabled={disabled || busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? 'Uploading…' : 'Attach files'}
      </button>

      {error ? <span className="attachment-error">{error}</span> : null}

      {files.length > 0 ? (
        <ul className="attachment-chips">
          {files.map((file) => (
            <li key={file.id}>
              <span className="truncate">{file.filename}</span>
              <span className="chip-size">{formatBytes(file.size_bytes)}</span>
              <button
                type="button"
                aria-label={`Remove ${file.filename}`}
                onClick={() => onChange(files.filter((f) => f.id !== file.id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
