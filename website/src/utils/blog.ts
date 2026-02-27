import type { CollectionEntry } from 'astro:content';

type BlogPost = CollectionEntry<'blog'>;

export function getSortedPosts(posts: BlogPost[]): BlogPost[] {
  return posts
    .filter(p => import.meta.env.DEV || !p.data.draft)
    .sort((a, b) => b.data.pubDate.getTime() - a.data.pubDate.getTime());
}

export function getUniqueTags(posts: BlogPost[]): string[] {
  const tags = new Set<string>();
  for (const post of posts) {
    for (const tag of post.data.tags) {
      tags.add(tag);
    }
  }
  return [...tags].sort((a, b) => a.localeCompare(b));
}

export function getPostsByTag(posts: BlogPost[], tag: string): BlogPost[] {
  return posts.filter(p => p.data.tags.includes(tag));
}

export function slugifyTag(tag: string): string {
  return tag.toLowerCase().replace(/\s+/g, '-').replace(/[^\w-]/g, '');
}
