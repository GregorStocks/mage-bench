export function parseInternalsModelStatsSelectedModelKey(
  params: URLSearchParams,
  validModelKeys: Iterable<string>,
): string | null {
  const selectedModelKey = params.get('statsModel');
  if (selectedModelKey === null) {
    return null;
  }

  const validModelKeySet = new Set(validModelKeys);
  return validModelKeySet.has(selectedModelKey) ? selectedModelKey : null;
}

export function syncInternalsModelStatsSelectedModelKeyToUrl(
  url: URL,
  selectedModelKey: string | null,
): void {
  if (selectedModelKey === null) {
    url.searchParams.delete('statsModel');
  } else {
    url.searchParams.set('statsModel', selectedModelKey);
  }
}
