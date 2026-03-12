import type { CollectionEntry } from 'astro:content';

type BlogPost = CollectionEntry<'blog'>;

export function getSortedPosts(posts: BlogPost[]): BlogPost[] {
  const visible = posts.filter(p => import.meta.env.DEV || !p.data.draft);
  const sorted = visible.sort((a, b) => b.data.pubDate.getTime() - a.data.pubDate.getTime());
  for (let i = 0; i < sorted.length - 1; i++) {
    if (sorted[i].data.pubDate.getTime() === sorted[i + 1].data.pubDate.getTime()) {
      throw new Error(
        `Blog posts "${sorted[i].data.title}" and "${sorted[i + 1].data.title}" have the same pubDate. ` +
        `Add a time to disambiguate, e.g. "pubDate: 2026-02-26T14:00:00"`
      );
    }
  }
  return sorted;
}
