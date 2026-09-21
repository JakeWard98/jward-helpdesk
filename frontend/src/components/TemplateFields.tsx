import type { TemplateField } from '../api/types';

interface Props {
  fields: TemplateField[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  disabled?: boolean;
}

/** Renders a template's custom fields. Validation is repeated server-side. */
export default function TemplateFields({ fields, values, onChange, disabled }: Props) {
  if (fields.length === 0) return null;

  function set(key: string, value: unknown) {
    onChange({ ...values, [key]: value });
  }

  return (
    <div className="template-fields">
      {fields.map((field) => {
        const value = values[field.key];
        const id = `field-${field.key}`;
        return (
          <label className="field" key={field.key} htmlFor={id}>
            <span>
              {field.label}
              {field.required ? <em className="required"> *</em> : null}
            </span>

            {field.type === 'textarea' ? (
              <textarea
                id={id}
                rows={4}
                required={field.required}
                disabled={disabled}
                value={(value as string) ?? ''}
                onChange={(e) => set(field.key, e.target.value)}
              />
            ) : field.type === 'select' ? (
              <select
                id={id}
                required={field.required}
                disabled={disabled}
                value={(value as string) ?? ''}
                onChange={(e) => set(field.key, e.target.value)}
              >
                <option value="">Choose…</option>
                {field.options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : field.type === 'checkbox' ? (
              <input
                id={id}
                type="checkbox"
                className="checkbox"
                disabled={disabled}
                checked={Boolean(value)}
                onChange={(e) => set(field.key, e.target.checked)}
              />
            ) : (
              <input
                id={id}
                type={field.type === 'number' ? 'number' : field.type === 'date' ? 'date' : 'text'}
                required={field.required}
                disabled={disabled}
                value={(value as string) ?? ''}
                onChange={(e) => set(field.key, e.target.value)}
              />
            )}

            {field.help ? <small className="field-help">{field.help}</small> : null}
          </label>
        );
      })}
    </div>
  );
}
