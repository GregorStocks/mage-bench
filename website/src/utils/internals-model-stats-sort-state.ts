export type InternalsModelStatsSortType = 'string' | 'number';

export interface InternalsModelStatsSortState {
  column: string;
  ascending: boolean;
}

export function defaultInternalsModelStatsSortState(): InternalsModelStatsSortState {
  return {
    column: 'timeoutRate',
    ascending: false,
  };
}

export function isValidInternalsModelStatsSortColumn(
  column: string,
  sortTypes: Record<string, InternalsModelStatsSortType>,
): boolean {
  return column in sortTypes;
}

export function parseInternalsModelStatsSortState(
  params: URLSearchParams,
  sortTypes: Record<string, InternalsModelStatsSortType>,
): InternalsModelStatsSortState {
  const fallback = defaultInternalsModelStatsSortState();
  const column = params.get('sort');
  if (!column || !isValidInternalsModelStatsSortColumn(column, sortTypes)) {
    return fallback;
  }

  const dir = params.get('dir');
  if (dir === 'asc') return { column, ascending: true };
  if (dir === 'desc') return { column, ascending: false };

  return {
    column,
    ascending: sortTypes[column] === 'string',
  };
}

export function syncInternalsModelStatsSortStateToUrl(
  url: URL,
  sortState: InternalsModelStatsSortState,
): void {
  const fallback = defaultInternalsModelStatsSortState();
  if (sortState.column === fallback.column && sortState.ascending === fallback.ascending) {
    url.searchParams.delete('sort');
    url.searchParams.delete('dir');
    return;
  }

  url.searchParams.set('sort', sortState.column);
  url.searchParams.set('dir', sortState.ascending ? 'asc' : 'desc');
}
