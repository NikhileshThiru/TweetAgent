import { useCallback, useEffect, useRef, useState } from 'react';
import { supabase } from '../lib/supabase';
import type { Draft, DraftStatus } from '../types';

const POLL_MS = 30_000;

/**
 * Loads drafts and keeps them fresh via 30s polling + a refetch whenever the
 * window regains focus (so opening the tab shows the latest immediately).
 * Mutations update local state optimistically, then reconcile on error.
 */
export function useDrafts() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const fetchDrafts = useCallback(async () => {
    const { data, error } = await supabase
      .from('tweet_drafts')
      .select('*')
      .order('created_at', { ascending: false });
    if (!mounted.current) return;
    if (error) {
      setError(error.message);
    } else {
      setDrafts(data as Draft[]);
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    mounted.current = true;
    void fetchDrafts();
    const interval = setInterval(() => void fetchDrafts(), POLL_MS);
    const onFocus = () => void fetchDrafts();
    window.addEventListener('focus', onFocus);
    return () => {
      mounted.current = false;
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, [fetchDrafts]);

  const setStatus = useCallback(
    async (id: string, status: DraftStatus) => {
      setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, status } : d)));
      const { error } = await supabase.from('tweet_drafts').update({ status }).eq('id', id);
      if (error) {
        setError(error.message);
        void fetchDrafts();
      }
    },
    [fetchDrafts],
  );

  const updateText = useCallback(
    async (id: string, text: string) => {
      setDrafts((prev) =>
        prev.map((d) => (d.id === id ? { ...d, text, char_count: text.length } : d)),
      );
      const { error } = await supabase.from('tweet_drafts').update({ text }).eq('id', id);
      if (error) {
        setError(error.message);
        void fetchDrafts();
      }
    },
    [fetchDrafts],
  );

  const updateFeedback = useCallback(
    async (id: string, feedback: string) => {
      const value = feedback.trim() || null;
      setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, feedback: value } : d)));
      const { error } = await supabase.from('tweet_drafts').update({ feedback: value }).eq('id', id);
      if (error) {
        setError(error.message);
        void fetchDrafts();
      }
    },
    [fetchDrafts],
  );

  const remove = useCallback(
    async (id: string) => {
      setDrafts((prev) => prev.filter((d) => d.id !== id));
      const { error } = await supabase.from('tweet_drafts').delete().eq('id', id);
      if (error) {
        setError(error.message);
        void fetchDrafts();
      }
    },
    [fetchDrafts],
  );

  return {
    drafts,
    loading,
    error,
    setStatus,
    updateText,
    updateFeedback,
    remove,
    refetch: fetchDrafts,
  };
}
