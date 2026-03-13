export function safeHttpUrl(rawUrl: string): string {
  const parsed = new URL(rawUrl);
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`youtubeUrl must use http or https, got ${parsed.protocol}`);
  }
  return parsed.toString();
}
