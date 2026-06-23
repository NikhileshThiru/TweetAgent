import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';

/** Persistent "standing instructions" injected into every generation.
 *  Stored as a single row (id=1) in app_settings. Fails soft if the table
 *  doesn't exist yet (i.e. the settings migration hasn't been run). */
export function useSteering() {
  const [steering, setSteering] = useState('');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    void supabase
      .from('app_settings')
      .select('steering')
      .eq('id', 1)
      .maybeSingle()
      .then(({ data }) => {
        if (!active) return;
        setSteering(data?.steering ?? '');
        setReady(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const save = useCallback(async (text: string): Promise<{ error?: string }> => {
    setSteering(text);
    const { error } = await supabase.from('app_settings').upsert({ id: 1, steering: text });
    return error ? { error: error.message } : {};
  }, []);

  return { steering, save, ready };
}
