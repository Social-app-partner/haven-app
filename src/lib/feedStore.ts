/**
 * feedStore.ts — glue between the Haven mood-feed backend and the React UI.
 *
 * - Builds the FeedContext from the existing local stores (moodStore +
 *   the `haven.settings` privacy preferences).
 * - Maps backend feed items (RankedPost / extra-care-card) into the shapes
 *   Home.tsx already renders (PostCard `Post` + an extra-care-card sentinel).
 * - Pure mapping functions are exported so they can be sanity-tested in Node.
 */

import type { FeedContext, FeedItem, RankedPost } from '@/lib/feedClient';
import { moodMeta, type MoodValue } from '@/lib/moodStore';
import type { Post } from '@/components/PostCard';

/* ---------- moodChip label -> MoodValue (reverse of the backend labels) ---------- */

const CHIP_LABEL_TO_MOOD: Record<string, MoodValue> = {
  'Feeling heavy': 'struggling',
  Anxious: 'low',
  Calm: 'okay',
  Hopeful: 'good',
  Great: 'great',
};

export function chipLabelToMood(label?: string): MoodValue | undefined {
  return label ? CHIP_LABEL_TO_MOOD[label] : undefined;
}

/* ---------- createdAt (ISO) -> display time ('now', '5m', '2h', '3d') ---------- */

export function relativeTime(createdAt: string, nowMs: number = Date.now()): string {
  const ageMs = Math.max(0, nowMs - Date.parse(createdAt));
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

/* ---------- RankedPost -> PostCard Post ---------- */

export function rankedPostToPost(rp: RankedPost): Post {
  const mood = chipLabelToMood(rp.moodChip);
  const meta = mood ? moodMeta(mood) : undefined;
  return {
    id: rp.id,
    author: rp.author,
    avatarColor: rp.avatarColor,
    time: relativeTime(rp.createdAt),
    moodChip:
      rp.moodChip && meta ? { label: rp.moodChip, bg: meta.chipBg, text: meta.chipText } : undefined,
    title: rp.title,
    body: rp.body,
    hugs: rp.hugs,
    replies: rp.replies,
    crisisResource: rp.crisisResource,
    contentWarning: rp.contentWarning,
  };
}

/* ---------- Feed items -> Home render entries ---------- */

export type FeedEntry = { kind: 'post'; post: Post } | { kind: 'extra-care-card'; reason: string };

export function mapFeedItems(items: FeedItem[]): FeedEntry[] {
  return items.map((item) =>
    item.type === 'extra-care-card'
      ? { kind: 'extra-care-card', reason: item.reason }
      : { kind: 'post', post: rankedPostToPost(item) },
  );
}

/* ---------- FeedContext built from local stores ---------- */

/** Reads `haven.settings` — showSensitive mirrors the "Content warnings" toggle. */
function readShowSensitive(): boolean | undefined {
  try {
    const raw = localStorage.getItem('haven.settings');
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as { contentWarnings?: boolean };
    return typeof parsed.contentWarnings === 'boolean' ? parsed.contentWarnings : undefined;
  } catch {
    return undefined;
  }
}

export function buildFeedContext(input: {
  userMood?: MoodValue;
  consecutiveLowDays?: number;
}): FeedContext {
  const ctx: FeedContext = {};
  if (input.userMood) ctx.userMood = input.userMood;
  if (typeof input.consecutiveLowDays === 'number') ctx.consecutiveLowDays = input.consecutiveLowDays;
  const showSensitive = readShowSensitive();
  if (showSensitive !== undefined) ctx.showSensitive = showSensitive;
  return ctx;
}
