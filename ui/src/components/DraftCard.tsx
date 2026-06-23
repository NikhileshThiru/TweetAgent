import { useState } from 'react';
import type { Draft, DraftStatus } from '../types';

const MAX = 280;

interface Props {
  draft: Draft;
  onStatus: (id: string, status: DraftStatus) => void;
  onUpdateText: (id: string, text: string) => void;
  onFeedback: (id: string, feedback: string) => void;
  onRemove: (id: string) => void;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** topic_hint is stored as "Source: Headline | https://url". Split it for display. */
function parseHint(hint: string | null): { label: string; url: string | null } | null {
  if (!hint) return null;
  const sep = hint.lastIndexOf(' | ');
  if (sep === -1) return { label: hint, url: null };
  const url = hint.slice(sep + 3).trim();
  return { label: hint.slice(0, sep).trim(), url: url.startsWith('http') ? url : null };
}

export function DraftCard({ draft, onStatus, onUpdateText, onFeedback, onRemove }: Props) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.text);
  const [copied, setCopied] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState(draft.feedback ?? '');

  const hint = parseHint(draft.topic_hint);
  const count = editing ? text.length : draft.char_count;
  const over = count > MAX;
  const warn = count > 240 && count <= MAX;

  async function copy() {
    await navigator.clipboard.writeText(draft.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function saveEdit() {
    const trimmed = text.trim();
    if (trimmed && trimmed !== draft.text) onUpdateText(draft.id, trimmed);
    setEditing(false);
  }

  function cancelEdit() {
    setText(draft.text);
    setEditing(false);
  }

  function saveNote() {
    if (note.trim() !== (draft.feedback ?? '')) onFeedback(draft.id, note);
    setNoteOpen(false);
  }

  return (
    <div className={`card s-${draft.status}`}>
      {editing ? (
        <textarea
          className={`edit ${over ? 'over' : ''}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          autoFocus
        />
      ) : (
        <p className="tweet">{draft.text}</p>
      )}

      <div className="card-meta">
        <span className={`count ${over ? 'over' : warn ? 'warn' : ''}`}>
          {count} / {MAX}
        </span>
        <span className="time" title={new Date(draft.created_at).toString()}>
          {formatTime(draft.created_at)}
        </span>
      </div>

      {hint && (
        <div className="hint">
          {hint.url ? (
            <a href={hint.url} target="_blank" rel="noreferrer">
              {hint.label}
            </a>
          ) : (
            hint.label
          )}
        </div>
      )}

      <div className="actions">
        {editing ? (
          <>
            <button className="btn primary" onClick={saveEdit} disabled={over || !text.trim()}>
              Save
            </button>
            <button className="btn" onClick={cancelEdit}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button className="btn" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button className="btn" onClick={copy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              className="btn"
              onClick={() => setNoteOpen((v) => !v)}
              title="Leave feedback that steers future drafts"
            >
              {draft.feedback ? 'Note •' : 'Note'}
            </button>

            {draft.status === 'pending' && (
              <>
                <button className="btn good" onClick={() => onStatus(draft.id, 'approved')}>
                  Approve
                </button>
                <button className="btn bad" onClick={() => onStatus(draft.id, 'trashed')}>
                  Trash
                </button>
              </>
            )}

            {draft.status === 'approved' && (
              <>
                <button className="btn good" onClick={() => onStatus(draft.id, 'posted')}>
                  Mark posted
                </button>
                <button className="btn" onClick={() => onStatus(draft.id, 'pending')}>
                  To pending
                </button>
                <button className="btn bad" onClick={() => onStatus(draft.id, 'trashed')}>
                  Trash
                </button>
              </>
            )}

            {draft.status === 'posted' && (
              <button className="btn" onClick={() => onStatus(draft.id, 'approved')}>
                Unmark posted
              </button>
            )}

            {draft.status === 'trashed' && (
              <>
                <button className="btn good" onClick={() => onStatus(draft.id, 'pending')}>
                  Restore
                </button>
                <button className="btn bad" onClick={() => onRemove(draft.id)}>
                  Delete forever
                </button>
              </>
            )}
          </>
        )}
      </div>

      {noteOpen && (
        <div className="note">
          <input
            type="text"
            value={note}
            placeholder="e.g. too corporate / more sarcastic / love this angle"
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveNote();
              if (e.key === 'Escape') {
                setNote(draft.feedback ?? '');
                setNoteOpen(false);
              }
            }}
            autoFocus
          />
          <button className="btn primary sm" onClick={saveNote}>
            Save
          </button>
        </div>
      )}
    </div>
  );
}
