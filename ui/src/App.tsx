import { useEffect, useMemo, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from './lib/supabase';
import { Auth } from './components/Auth';
import { DraftCard } from './components/DraftCard';
import { Steering } from './components/Steering';
import { useDrafts } from './hooks/useDrafts';
import { useSteering } from './hooks/useSteering';
import type { DraftStatus } from './types';

const TABS: { key: DraftStatus; label: string }[] = [
  { key: 'pending', label: 'Pending' },
  { key: 'approved', label: 'Approved' },
  { key: 'posted', label: 'Posted' },
  { key: 'trashed', label: 'Trashed' },
];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  if (!ready) return <div className="center muted">Loading…</div>;
  if (!session) return <Auth />;
  return <Dashboard email={session.user.email ?? ''} />;
}

type GenState = 'idle' | 'working' | 'error';

function Dashboard({ email }: { email: string }) {
  const { drafts, loading, error, setStatus, updateText, updateFeedback, remove, refetch } =
    useDrafts();
  const { steering, save: saveSteering } = useSteering();
  const [tab, setTab] = useState<DraftStatus>('pending');
  const [gen, setGen] = useState<GenState>('idle');
  const [steerOpen, setSteerOpen] = useState(false);

  const counts = useMemo(() => {
    const c: Record<DraftStatus, number> = { pending: 0, approved: 0, posted: 0, trashed: 0 };
    for (const d of drafts) c[d.status]++;
    return c;
  }, [drafts]);

  const visible = drafts.filter((d) => d.status === tab);

  async function generateNow() {
    setGen('working');
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) throw new Error('not signed in');
      // Calls the Vercel function directly — generation runs synchronously
      // (~10-15s for a 4-topic spread), then the drafts are already in Supabase.
      const r = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ mode: 'spread' }),
      });
      if (!r.ok) throw new Error(await r.text());
      await refetch();
    } catch {
      setGen('error');
      setTimeout(() => setGen('idle'), 4_000);
      return;
    }
    setGen('idle');
  }

  const genLabel =
    gen === 'working' ? 'Generating…' : gen === 'error' ? 'Failed — retry' : 'Generate now';

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <span className="brand-dot" />
          Tweet Drafts
        </span>
        <div className="topbar-right">
          <button
            className="btn primary sm"
            onClick={() => void generateNow()}
            disabled={gen === 'working'}
            title="Generate a fresh spread across all your topics now"
          >
            {genLabel}
          </button>
          <button
            className={`btn ghost ${steerOpen ? 'active' : ''}`}
            onClick={() => setSteerOpen((v) => !v)}
            title="Standing instructions for every draft"
          >
            Steering
          </button>
          <button className="btn ghost" onClick={() => void refetch()} title="Refresh">
            Refresh
          </button>
          <button className="btn ghost" onClick={() => void supabase.auth.signOut()} title={email}>
            Sign out
          </button>
        </div>
      </header>
      {gen === 'working' && (
        <p className="muted center-text" style={{ padding: '8px 0 0' }}>
          Generating a spread across your topics… (~10-15s)
        </p>
      )}

      {steerOpen && (
        <Steering initial={steering} onSave={saveSteering} onClose={() => setSteerOpen(false)} />
      )}

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            <span className="badge">{counts[t.key]}</span>
          </button>
        ))}
      </nav>

      {error && <p className="error banner">{error}</p>}

      <main className="list">
        {loading ? (
          <p className="muted center-text">Loading drafts…</p>
        ) : visible.length === 0 ? (
          <p className="muted center-text">Nothing in {tab}.</p>
        ) : (
          visible.map((d) => (
            <DraftCard
              key={d.id}
              draft={d}
              onStatus={setStatus}
              onUpdateText={updateText}
              onFeedback={updateFeedback}
              onRemove={remove}
            />
          ))
        )}
      </main>
    </div>
  );
}
