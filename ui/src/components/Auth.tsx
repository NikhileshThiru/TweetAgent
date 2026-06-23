import { useState } from 'react';
import type { FormEvent } from 'react';
import { supabase } from '../lib/supabase';

/** Magic-link sign-in. RLS restricts the table to the owner's email, so only
 *  you can see or change anything even after signing in. */
export function Auth() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { emailRedirectTo: window.location.origin },
    });
    setLoading(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  return (
    <div className="center">
      <div className="auth-card">
        <h1 className="brand">
          <span className="brand-dot" />
          Tweet Drafts
        </h1>
        {sent ? (
          <p className="muted">
            Check <strong>{email}</strong> for a magic link. Open it on this device to sign in.
          </p>
        ) : (
          <form onSubmit={send} className="auth-form">
            <input
              type="email"
              required
              placeholder="you@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
            <button type="submit" className="btn primary" disabled={loading || !email.trim()}>
              {loading ? 'Sending…' : 'Send magic link'}
            </button>
            {error && <p className="error">{error}</p>}
          </form>
        )}
      </div>
    </div>
  );
}
