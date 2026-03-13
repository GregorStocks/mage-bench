export interface InternalsTrendsModelFilterState {
  selectedModelKeys: string[];
  search: string;
}

function orderedKnownModelKeys(allModelKeys: Iterable<string>): string[] {
  return Array.from(new Set(allModelKeys));
}

export function parseInternalsTrendsModelFilterState(
  params: URLSearchParams,
  allModelKeys: Iterable<string>,
): InternalsTrendsModelFilterState {
  const knownKeys = orderedKnownModelKeys(allModelKeys);
  const knownKeySet = new Set(knownKeys);
  const encodedSelection = params.get('models');

  let selectedModelKeys = knownKeys;
  if (encodedSelection === 'none') {
    selectedModelKeys = [];
  } else if (encodedSelection !== null) {
    const requestedKeys = new Set(
      encodedSelection
        .split(',')
        .filter((key) => key !== '' && knownKeySet.has(key)),
    );
    const sanitizedSelection = knownKeys.filter((key) => requestedKeys.has(key));
    if (sanitizedSelection.length > 0) {
      selectedModelKeys = sanitizedSelection;
    }
  }

  return {
    selectedModelKeys,
    search: params.get('modelSearch') ?? '',
  };
}

export function syncInternalsTrendsModelFilterStateToUrl(
  url: URL,
  state: InternalsTrendsModelFilterState,
  allModelKeys: Iterable<string>,
): void {
  const knownKeys = orderedKnownModelKeys(allModelKeys);
  const selectedKeySet = new Set(state.selectedModelKeys);
  const selectedModelKeys = knownKeys.filter((key) => selectedKeySet.has(key));

  if (selectedModelKeys.length === knownKeys.length) {
    url.searchParams.delete('models');
  } else if (selectedModelKeys.length === 0) {
    url.searchParams.set('models', 'none');
  } else {
    url.searchParams.set('models', selectedModelKeys.join(','));
  }

  if (state.search === '') {
    url.searchParams.delete('modelSearch');
  } else {
    url.searchParams.set('modelSearch', state.search);
  }
}
