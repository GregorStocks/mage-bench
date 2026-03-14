import {
  parseInternalsModelStatsSortState,
  syncInternalsModelStatsSortStateToUrl,
} from '../../utils/internals-model-stats-sort-state';
import {
  parseInternalsModelStatsSelectedModelKey,
  syncInternalsModelStatsSelectedModelKeyToUrl,
} from '../../utils/internals-model-stats-selection-state';
import type {
  ModelStatsData,
  ModelStatsEpochBucket,
  ModelStatsModel,
} from '../../utils/internals-types';
import type { InternalsDashboardController } from './dashboard-controller';
import { getRequiredElement } from './dom';
import { fetchJson } from './fetch-json';

const TOKEN_DETAIL_EPOCH = 18;
const UNRELIABLE_CACHE_MODELS = new Set(['x-ai/grok-4.1-fast']);

interface AggregatedStats {
  key: string;
  modelName: string;
  provider: string;
  gamesPlayed: number;
  totalTimeouts: number;
  totalOtherErrors: number;
  timeoutRate: number;
  contextResets: number;
  successfulResponses: number;
  avgPromptTokens: number;
  avgCompletionTokens: number;
  avgCost: number;
  cacheRate: number;
  reasoningPct: number;
  hasPreTokenDetailData: boolean;
  latencyP50: number;
  latencyP95: number;
  errors: Record<string, number>;
}

function hasUnreliableCache(key: string): boolean {
  const modelId = key.split('::')[0];
  return UNRELIABLE_CACHE_MODELS.has(modelId);
}

function timeoutRateClass(rate: number): string {
  if (rate < 0.02) {
    return 'rate-good';
  }
  if (rate < 0.10) {
    return 'rate-ok';
  }
  return 'rate-bad';
}

function esc(value: string): string {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

export async function initInternalsModelStatsTab(options: {
  controller: InternalsDashboardController;
  dataUrl: string;
  root: HTMLElement;
}): Promise<void> {
  const { controller, dataUrl, root } = options;
  const tbody = getRequiredElement<HTMLTableSectionElement>(root, '#stats-body');

  let rawData: ModelStatsData;
  try {
    rawData = await fetchJson<ModelStatsData>(dataUrl);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to load model stats.';
    tbody.innerHTML = `<tr><td colspan="14" class="empty">${esc(message)}</td></tr>`;
    throw error;
  }

  const models = rawData.models;
  const modelKeys = Object.keys(models);
  const minEpochInput = getRequiredElement<HTMLInputElement>(root, '#min-epoch');
  const searchInput = getRequiredElement<HTMLInputElement>(root, '#search-input');
  const noResults = getRequiredElement<HTMLElement>(root, '#no-results');
  const tokenDetailFootnote = getRequiredElement<HTMLElement>(root, '#token-detail-footnote');
  const errorDetail = getRequiredElement<HTMLElement>(root, '#error-detail');
  const errorDetailHeading = getRequiredElement<HTMLElement>(root, '#error-detail-heading');
  const errorDetailBody = getRequiredElement<HTMLTableSectionElement>(root, '#error-detail-body');
  const headers = root.querySelectorAll<HTMLElement>('#stats-table th[data-sort]');
  const sortTypes = Object.fromEntries(
    Array.from(headers, (header) => [header.dataset.sort!, header.dataset.type!]),
  ) as Record<string, 'string' | 'number'>;

  let currentSort = 'timeoutRate';
  let currentSortAsc = false;
  let selectedModelKey: string | null = null;

  function aggregate(minEpoch: number): AggregatedStats[] {
    const results: AggregatedStats[] = [];

    for (const [key, model] of Object.entries(models) as Array<[string, ModelStatsModel]>) {
      let gamesPlayed = 0;
      let totalCost = 0;
      let totalPrompt = 0;
      let totalCompletion = 0;
      let successfulResponses = 0;
      let totalCached = 0;
      let totalReasoning = 0;
      let hasPreTokenDetailData = false;
      let contextResets = 0;
      let weightedP50 = 0;
      let weightedP95 = 0;
      let totalSamples = 0;
      const errors: Record<string, number> = {};

      for (const [epochStr, bucket] of Object.entries(model.epochs) as Array<[string, ModelStatsEpochBucket]>) {
        const epochNum = parseInt(epochStr, 10);
        if (epochNum < minEpoch) {
          continue;
        }
        if (epochNum < TOKEN_DETAIL_EPOCH) {
          hasPreTokenDetailData = true;
        }

        gamesPlayed += bucket.gamesPlayed;
        totalCost += bucket.totalCostUsd;
        totalPrompt += bucket.totalPromptTokens;
        totalCompletion += bucket.totalCompletionTokens;
        totalCached += bucket.totalCachedTokens ?? 0;
        totalReasoning += bucket.totalReasoningTokens ?? 0;
        successfulResponses += bucket.successfulResponses;
        contextResets += bucket.contextResets;

        const samples = bucket.latencySamples ?? 0;
        if (samples > 0) {
          weightedP50 += (bucket.latencyP50 ?? 0) * samples;
          weightedP95 += (bucket.latencyP95 ?? 0) * samples;
          totalSamples += samples;
        }

        for (const [errorType, count] of Object.entries(bucket.errors)) {
          errors[errorType] = (errors[errorType] ?? 0) + count;
        }
      }

      if (gamesPlayed === 0) {
        continue;
      }

      const totalTimeouts = errors.timeout ?? 0;
      const totalOtherErrors = Object.entries(errors)
        .filter(([errorType]) => errorType !== 'timeout')
        .reduce((sum, [, count]) => sum + count, 0);
      const totalAttempts = totalTimeouts + successfulResponses;

      results.push({
        key,
        modelName: model.modelName,
        provider: model.provider,
        gamesPlayed,
        totalTimeouts,
        totalOtherErrors,
        timeoutRate: totalAttempts > 0 ? totalTimeouts / totalAttempts : 0,
        contextResets,
        successfulResponses,
        avgPromptTokens: successfulResponses > 0 ? totalPrompt / successfulResponses : 0,
        avgCompletionTokens: successfulResponses > 0 ? totalCompletion / successfulResponses : 0,
        avgCost: gamesPlayed > 0 ? totalCost / gamesPlayed : 0,
        cacheRate: hasUnreliableCache(key) ? -1 : (totalPrompt > 0 ? totalCached / totalPrompt : 0),
        reasoningPct: totalCompletion > 0 ? totalReasoning / totalCompletion : 0,
        hasPreTokenDetailData,
        latencyP50: totalSamples > 0 ? weightedP50 / totalSamples : 0,
        latencyP95: totalSamples > 0 ? weightedP95 / totalSamples : 0,
        errors,
      });
    }

    return results;
  }

  function setSortedHeaderState(header: HTMLElement, ascending: boolean): void {
    headers.forEach((candidate) => {
      candidate.classList.remove('sorted', 'asc', 'desc');
      const arrow = candidate.querySelector('.sort-arrow');
      if (arrow) {
        arrow.textContent = '';
      }
    });

    header.classList.add('sorted', ascending ? 'asc' : 'desc');
    let arrow = header.querySelector('.sort-arrow');
    if (!arrow) {
      arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      header.appendChild(arrow);
    }
    arrow.textContent = ascending ? '\u25B2' : '\u25BC';
  }

  function syncModelStatsStateToUrl(): void {
    if (controller.getActiveTabName() !== 'model-stats') {
      return;
    }

    controller.replaceUrlForTab('model-stats', (url) => {
      syncInternalsModelStatsSortStateToUrl(url, {
        column: currentSort,
        ascending: currentSortAsc,
      });
      syncInternalsModelStatsSelectedModelKeyToUrl(url, selectedModelKey);
    });
  }

  controller.registerTabUrlSyncHandler('model-stats', syncModelStatsStateToUrl);

  function renderErrorDetail(stats: AggregatedStats): void {
    errorDetailHeading.textContent = `Error Breakdown: ${stats.modelName}`;
    errorDetailBody.innerHTML = '';

    const entries = Object.entries(stats.errors).sort((left, right) => right[1] - left[1]);
    if (entries.length === 0) {
      errorDetailBody.innerHTML = '<tr><td colspan="2" class="empty">No errors</td></tr>';
    } else {
      for (const [errorType, count] of entries) {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${esc(errorType)}</td><td class="num">${count}</td>`;
        errorDetailBody.appendChild(row);
      }
    }

    errorDetail.style.display = '';
  }

  function showErrorDetail(stats: AggregatedStats): void {
    selectedModelKey = stats.key;
    tbody.querySelectorAll('tr').forEach((row) => row.classList.remove('selected'));
    const row = tbody.querySelector(`tr[data-key="${CSS.escape(stats.key)}"]`);
    if (row) {
      row.classList.add('selected');
    }
    renderErrorDetail(stats);
    syncModelStatsStateToUrl();
  }

  function render(): void {
    const minEpoch = parseInt(minEpochInput.value, 10) || 1;
    const searchTerm = searchInput.value.toLowerCase().trim();
    let stats = aggregate(minEpoch);

    if (searchTerm) {
      stats = stats.filter((entry) => entry.modelName.toLowerCase().includes(searchTerm));
    }

    stats.sort((left, right) => {
      const leftValue = left[currentSort as keyof AggregatedStats];
      const rightValue = right[currentSort as keyof AggregatedStats];
      if (typeof leftValue === 'string' && typeof rightValue === 'string') {
        const cmp = leftValue.toLowerCase().localeCompare(rightValue.toLowerCase());
        return currentSortAsc ? cmp : -cmp;
      }
      if (typeof leftValue !== 'number' || typeof rightValue !== 'number') {
        throw new Error(`Unsupported sort column type: ${currentSort}`);
      }
      return currentSortAsc ? leftValue - rightValue : rightValue - leftValue;
    });

    tbody.innerHTML = '';
    for (const statsEntry of stats) {
      const row = document.createElement('tr');
      row.dataset.key = statsEntry.key;
      if (statsEntry.key === selectedModelKey) {
        row.classList.add('selected');
      }
      row.innerHTML = `
        <td class="model-name">${esc(statsEntry.modelName)}</td>
        <td>${esc(statsEntry.provider)}</td>
        <td class="num">${statsEntry.gamesPlayed}</td>
        <td class="num"><span class="${timeoutRateClass(statsEntry.timeoutRate)}">${(statsEntry.timeoutRate * 100).toFixed(1)}%</span></td>
        <td class="num">${statsEntry.totalTimeouts}</td>
        <td class="num">${statsEntry.totalOtherErrors}</td>
        <td class="num">${statsEntry.contextResets}</td>
        <td class="num">${Math.round(statsEntry.avgPromptTokens).toLocaleString()}</td>
        <td class="num">${Math.round(statsEntry.avgCompletionTokens).toLocaleString()}</td>
        <td class="num">$${statsEntry.avgCost.toFixed(2)}</td>
        <td class="num">${statsEntry.cacheRate < 0 ? '*' : statsEntry.cacheRate > 0 ? `${(statsEntry.cacheRate * 100).toFixed(1)}%` : '-'}${statsEntry.hasPreTokenDetailData ? '*' : ''}</td>
        <td class="num">${statsEntry.reasoningPct > 0 ? `${(statsEntry.reasoningPct * 100).toFixed(1)}%` : '-'}${statsEntry.hasPreTokenDetailData ? '*' : ''}</td>
        <td class="num">${statsEntry.latencyP50.toFixed(1)}</td>
        <td class="num">${statsEntry.latencyP95.toFixed(1)}</td>
      `;
      row.addEventListener('click', () => showErrorDetail(statsEntry));
      tbody.appendChild(row);
    }

    noResults.style.display = stats.length === 0 ? '' : 'none';
    tokenDetailFootnote.style.display = stats.some((entry) => entry.hasPreTokenDetailData) ? '' : 'none';

    if (selectedModelKey) {
      const selected = stats.find((entry) => entry.key === selectedModelKey);
      if (selected) {
        renderErrorDetail(selected);
      } else {
        errorDetail.style.display = 'none';
        selectedModelKey = null;
        syncModelStatsStateToUrl();
      }
    }
  }

  headers.forEach((header) => {
    header.addEventListener('click', () => {
      const column = header.dataset.sort;
      const type = header.dataset.type;
      if (!column || !type) {
        throw new Error('Model stats header is missing sort metadata');
      }

      const wasSorted = header.classList.contains('sorted');
      const wasAsc = header.classList.contains('asc');
      const ascending = wasSorted ? !wasAsc : type === 'string';

      setSortedHeaderState(header, ascending);
      currentSort = column;
      currentSortAsc = ascending;
      syncModelStatsStateToUrl();
      render();
    });
  });

  {
    const params = new URLSearchParams(window.location.search);
    const sortState = parseInternalsModelStatsSortState(params, sortTypes);
    currentSort = sortState.column;
    currentSortAsc = sortState.ascending;
    selectedModelKey = parseInternalsModelStatsSelectedModelKey(params, modelKeys);

    const sortHeader = root.querySelector<HTMLElement>(`#stats-table th[data-sort="${sortState.column}"]`);
    if (!(sortHeader instanceof HTMLElement)) {
      throw new Error(`Missing model stats sort header for column: ${sortState.column}`);
    }
    setSortedHeaderState(sortHeader, sortState.ascending);
  }

  minEpochInput.addEventListener('input', render);
  searchInput.addEventListener('input', render);

  syncModelStatsStateToUrl();
  render();
}
