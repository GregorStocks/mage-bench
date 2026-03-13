export type RankingsSortType = 'string' | 'number';

export interface RankingsSortState {
  column: string;
  ascending: boolean;
}

export function defaultRankingsSortState(isExhibition: boolean): RankingsSortState {
  return {
    column: isExhibition ? 'winRate' : 'rating',
    ascending: false,
  };
}

export function isValidRankingsSortColumn(
  column: string,
  isExhibition: boolean,
  sortTypes: Record<string, RankingsSortType>,
): boolean {
  if (!(column in sortTypes)) return false;
  if (isExhibition && column === 'rating') return false;
  return true;
}

export function parseRankingsSortState(
  params: URLSearchParams,
  isExhibition: boolean,
  sortTypes: Record<string, RankingsSortType>,
): RankingsSortState {
  const fallback = defaultRankingsSortState(isExhibition);
  const column = params.get('sort');
  if (!column || !isValidRankingsSortColumn(column, isExhibition, sortTypes)) {
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

export function syncRankingsSortStateToUrl(
  url: URL,
  sortState: RankingsSortState,
  isExhibition: boolean,
): void {
  const fallback = defaultRankingsSortState(isExhibition);
  if (sortState.column === fallback.column && sortState.ascending === fallback.ascending) {
    url.searchParams.delete('sort');
    url.searchParams.delete('dir');
    return;
  }

  url.searchParams.set('sort', sortState.column);
  url.searchParams.set('dir', sortState.ascending ? 'asc' : 'desc');
}
