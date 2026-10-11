/**
 * feedClient.ts — typed client module for the Haven mood-feed backend.
 *
 * Types mirror moodStore.ts (MoodValue etc.) so it drops in with no adapter
 * layer. Base URL: VITE_HAVEN_API_URL, default http://localhost:4000.
 *
 * Usage:
 *   import { fetchFeed } from './feedClient';
 *   const feed = await fetchFeed({ userMood: todayMood, consecutiveLowDays });
 */

export type MoodValue = 'great' | 'good' | 'okay' | 'low' | 'struggling';

export interface FeedContext {
  userMood?: MoodValue;
  consecutiveLowDays?: number;
  showSensitive?: boolean;
  topN?: number;
}

export interface ScoreBreakdown {
  moodAffinity: number;
  recency: number;
  warmth: number;
  diversityPenalty: number;
  total: number;
}

export interface RankedPost {
  type: 'post';
  id: string;
  author: string;
  avatarColor: string;
  createdAt: string;
  moodChip?: string;
  title: string;
  body: string;
  hugs: number;
  replies: number;
  contentWarning?: string;
  score: ScoreBreakdown;
  /** When true, show crisis-support resources alongside this post. */
  crisisResource?: boolean;
}

/** Special pinned card after 3+ consecutive low days — render ExtraCareCard. */
export interface ExtraCareCardItem {
  type: 'extra-care-card';
  reason: string;
}

export type FeedItem = RankedPost | ExtraCareCardItem;

export interface FeedResponse {
  context: FeedContext;
  count: number;
  items: FeedItem[];
}

export interface MoodInfo {
  value: MoodValue;
  score: number;
  chipLabel: string;
}

const API_BASE =
  (typeof import.meta !== 'undefined' &&
    (import.meta as unknown as { env?: { VITE_HAVEN_API_URL?: string } }).env
      ?.VITE_HAVEN_API_URL) ||
  'http://localhost:4000';

export async function fetchFeed(context: FeedContext = {}): Promise<FeedResponse> {
  const res = await fetch(`${API_BASE}/api/feed`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(context),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `fetchFeed failed: ${res.status}`);
  }
  return (await res.json()) as FeedResponse;
}

export async function fetchMoods(): Promise<MoodInfo[]> {
  const res = await fetch(`${API_BASE}/api/moods`);
  if (!res.ok) throw new Error(`fetchMoods failed: ${res.status}`);
  const data = (await res.json()) as { moods: MoodInfo[] };
  return data.moods;
}

export interface NewPostInput {
  author: string;
  avatarColor?: string;
  title: string;
  body: string;
  moodChip?: string;
  contentWarning?: string;
}

export async function createPost(input: NewPostInput): Promise<RankedPost> {
  const res = await fetch(`${API_BASE}/api/posts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `createPost failed: ${res.status}`);
  }
  return (await res.json()) as RankedPost;
}
