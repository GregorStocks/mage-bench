export type InternalsTabName = 'trends' | 'model-stats' | 'blunder' | 'golden';

const TAB_PARAMS: Record<InternalsTabName, readonly string[]> = {
  trends: ['metric', 'format', 'yscale', 'minEpoch', 'models', 'modelSearch', 'display', 'bucket', 'smoothing'],
  'model-stats': ['sort', 'dir', 'statsModel'],
  blunder: ['bmetric', 'bminVer'],
  golden: [],
};

export function isInternalsTabName(tabName: string): tabName is InternalsTabName {
  return tabName in TAB_PARAMS;
}

export function syncInternalsTabToUrl(url: URL, activeTab: InternalsTabName): void {
  if (activeTab === 'trends') {
    url.searchParams.delete('tab');
  } else {
    url.searchParams.set('tab', activeTab);
  }

  for (const [tabName, params] of Object.entries(TAB_PARAMS) as [InternalsTabName, readonly string[]][]) {
    if (tabName === activeTab) continue;
    for (const param of params) {
      url.searchParams.delete(param);
    }
  }
}
