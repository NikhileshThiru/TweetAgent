export type DraftStatus = 'pending' | 'approved' | 'posted' | 'trashed';

export interface Draft {
  id: string;
  text: string;
  status: DraftStatus;
  topic_hint: string | null;
  model: string | null;
  feedback: string | null;
  image_url: string | null;
  char_count: number;
  created_at: string;
  updated_at: string;
}
