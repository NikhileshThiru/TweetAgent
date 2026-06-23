import { useState } from 'react';

interface Props {
  initial: string;
  onSave: (text: string) => Promise<{ error?: string }>;
  onClose: () => void;
}

type Status = 'idle' | 'saving' | 'saved' | 'error';

/** Always-on instructions for the generator — the home for general feedback
 *  like "never tweet about crypto" or "prefer one-liners". */
export function Steering({ initial, onSave, onClose }: Props) {
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<Status>('idle');
  const [err, setErr] = useState('');

  async function save() {
    setStatus('saving');
    const res = await onSave(text);
    if (res.error) {
      setErr(res.error);
      setStatus('error');
    } else {
      setStatus('saved');
      setTimeout(() => setStatus('idle'), 2200);
    }
  }

  return (
    <div className="steer">
      <h2>Steering — standing instructions</h2>
      <p>
        Applied to <strong>every</strong> draft and never forgotten. Use it for permanent rules —
        topics to avoid, tone, length. e.g. “Never tweet about crypto. Prefer one sentence. Be
        skeptical of AI hype.”
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Never tweet about crypto. Keep it to one sentence when you can. Don't be preachy."
      />
      <div className="steer-actions">
        <button className="btn primary sm" onClick={() => void save()} disabled={status === 'saving'}>
          {status === 'saving' ? 'Saving…' : 'Save'}
        </button>
        <button className="btn ghost" onClick={onClose}>
          Close
        </button>
        {status === 'saved' && <span className="steer-saved">Saved</span>}
        {status === 'error' && <span className="error">{err}</span>}
      </div>
    </div>
  );
}
