import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import { getSortedPosts } from '../utils/blog';

export async function GET(context: { site: URL }) {
  const posts = getSortedPosts(await getCollection('blog'));
  return rss({
    title: 'mage-bench blog',
    description: 'Updates and technical deep-dives from the mage-bench project.',
    site: context.site,
    items: posts.map(post => ({
      title: post.data.title,
      pubDate: post.data.pubDate,
      link: `/blog/${post.id}`,
    })),
  });
}
