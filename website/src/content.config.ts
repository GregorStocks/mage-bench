import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    updatedDate: z.coerce.date().optional(),
    author: z.string(),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
  }),
});

const reports = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/reports' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    gameId: z.string(),
    pubDate: z.coerce.date(),
    format: z.string(),
    winner: z.string().nullable(),
    players: z.array(z.object({
      name: z.string(),
      model: z.string(),
      deck: z.string().optional(),
    })),
    totalTurns: z.number(),
    draft: z.boolean().default(false),
  }),
});

export const collections = { blog, reports };
