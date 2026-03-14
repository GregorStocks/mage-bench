import { Chart, registerables, type ChartDataset } from 'chart.js';

import {
  parseInternalsTrendsModelFilterState,
  syncInternalsTrendsModelFilterStateToUrl,
} from '../../utils/internals-trends-model-filter-state';
import type {
  InternalsPlayerRecord,
  InternalsTrendData,
} from '../../utils/internals-types';
import type { InternalsDashboardController } from './dashboard-controller';
import { getRequiredElement } from './dom';
import { fetchJson } from './fetch-json';

Chart.register(...registerables);

const LINE_COLORS = [
  '#ff4444', '#4dc9f6', '#f7e430', '#2ecc71', '#cc66ff',
  '#ff9933', '#00d4aa', '#ff66aa', '#96cc33', '#6688ff',
  '#ff7766', '#33bbdd', '#ddaa22', '#33bb88', '#ee55cc',
  '#cc6633', '#55ddcc', '#dd4477', '#66bb33', '#9966ee',
  '#ff9988', '#77ccee', '#eebb44', '#66dd99', '#bb88ee',
  '#ffbb66', '#44eebb', '#ff88bb', '#bbdd55', '#8888dd',
];

const SYMLOG_CONSTANT = 1;
const UNRELIABLE_CACHE_MODELS = new Set(['x-ai/grok-4.1-fast']);

type YScaleMode = 'linear' | 'symlog';

type TrendPoint = {
  x: number;
  y: number;
  rawY: number;
  modelName: string;
  modelCount: number;
  format: string;
  gameId: string;
  dateLabel: string;
};

type PerGameMetric = {
  label: string;
  unit: string;
  compute: (_player: InternalsPlayerRecord) => number | null;
};

const METRICS: Record<string, PerGameMetric> = {
  costPerGame: {
    label: 'Cost per Game',
    unit: '$',
    compute: (player) => player.costUsd,
  },
  winRate: {
    label: 'Win Rate',
    unit: '%',
    compute: (player) => player.won ? 100 : 0,
  },
  toolFailRate: {
    label: 'Tool Fail Rate',
    unit: '%',
    compute: (player) => {
      const total = player.toolCallsOk + player.toolCallsFailed;
      return total > 0 ? (player.toolCallsFailed / total) * 100 : null;
    },
  },
  errorRate: {
    label: 'Error Rate',
    unit: '%',
    compute: (player) => {
      const total = player.responses + player.timeouts + player.otherErrors;
      return total > 0 ? (player.otherErrors / total) * 100 : null;
    },
  },
  latencyP50: {
    label: 'Latency p50',
    unit: 's',
    compute: (player) => player.latencyP50,
  },
  timerTimeoutRate: {
    label: 'Timer Timeout Rate',
    unit: '%',
    compute: (player) => player.timedOut ? 100 : 0,
  },
  timeoutRate: {
    label: 'Timeout Rate',
    unit: '%',
    compute: (player) => {
      const total = player.timeouts + player.responses;
      return total > 0 ? (player.timeouts / total) * 100 : null;
    },
  },
  cacheRate: {
    label: 'Cache Rate',
    unit: '%',
    compute: (player) => player.promptTokens > 0 && !hasUnreliableCache(player.key)
      ? (player.cachedTokens / player.promptTokens) * 100
      : null,
  },
  toolCallsPerGame: {
    label: 'Tool Calls per Game',
    unit: '',
    compute: (player) => player.toolCallsOk + player.toolCallsFailed,
  },
  avgPromptTokens: {
    label: 'Prompt Tokens per Response',
    unit: '',
    compute: (player) => player.responses > 0 ? player.promptTokens / player.responses : null,
  },
  avgCompletionTokens: {
    label: 'Completion Tokens per Response',
    unit: '',
    compute: (player) => player.responses > 0 ? player.completionTokens / player.responses : null,
  },
  resetsPerGame: {
    label: 'Context Resets',
    unit: '',
    compute: (player) => player.contextResets,
  },
  thinkingTimePerGame: {
    label: 'Thinking Time',
    unit: 's',
    compute: (player) => player.thinkingTimeSecs,
  },
  reasoningPct: {
    label: 'Reasoning Token %',
    unit: '%',
    compute: (player) => player.completionTokens > 0
      ? (player.reasoningTokens / player.completionTokens) * 100
      : null,
  },
};

function hasUnreliableCache(key: string): boolean {
  const modelId = key.split('::')[0];
  return UNRELIABLE_CACHE_MODELS.has(modelId);
}

function withAlpha(color: string, alphaHex: string): string {
  return color.startsWith('#') && color.length === 7 ? color + alphaHex : color;
}

function symlog(value: number): number {
  return Math.sign(value) * Math.log10(1 + Math.abs(value) / SYMLOG_CONSTANT);
}

function symlogInverse(value: number): number {
  return Math.sign(value) * SYMLOG_CONSTANT * (Math.pow(10, Math.abs(value)) - 1);
}

function formatMetricValue(value: number, unit: string): string {
  if (unit === '%') {
    return `${value.toFixed(1)}%`;
  }
  if (unit === '$') {
    return `$${value.toFixed(value >= 10 ? 1 : 2)}`;
  }
  if (unit === 's') {
    return `${value.toFixed(1)}s`;
  }
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  if (Number.isInteger(value)) {
    return value.toLocaleString('en-US');
  }
  return value.toFixed(1);
}

function formatDateTimeLabel(ts: number): string {
  return new Date(ts).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatTimeTick(ts: number, rangeMs: number): string {
  if (!Number.isFinite(ts)) {
    return '';
  }
  if (rangeMs > 3 * 24 * 60 * 60 * 1000) {
    return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric' });
}

function setActiveButton(root: HTMLElement, selector: string, attr: string, value: string): void {
  root.querySelectorAll<HTMLElement>(selector).forEach((button) => {
    button.classList.toggle('active', button.dataset[attr] === value);
  });
}

export async function initInternalsTrendsTab(options: {
  controller: InternalsDashboardController;
  dataUrl: string;
  root: HTMLElement;
}): Promise<void> {
  const { controller, dataUrl, root } = options;
  const noteEl = getRequiredElement<HTMLElement>(root, '#chart-note');

  let trendData: InternalsTrendData;
  try {
    trendData = await fetchJson<InternalsTrendData>(dataUrl);
  } catch (error) {
    noteEl.textContent = error instanceof Error ? error.message : 'Failed to load trend data.';
    throw error;
  }

  const modelMap = new Map<string, string>();
  for (const game of trendData.games) {
    for (const player of game.players) {
      if (!modelMap.has(player.key)) {
        modelMap.set(player.key, player.modelName);
      }
    }
  }
  const allModels = Array.from(modelMap.entries()).sort((left, right) => left[1].localeCompare(right[1]));
  const allModelKeys = allModels.map(([key]) => key);

  let currentMetric = 'costPerGame';
  let currentFormat = 'all';
  let selectedModels = new Set(allModelKeys);
  let minEpochTrend = 1;
  let yScaleMode: YScaleMode = 'linear';

  const checkboxContainer = getRequiredElement<HTMLElement>(root, '#model-checkboxes');
  const modelSearch = getRequiredElement<HTMLInputElement>(root, '#model-search');
  const minEpochTrendInput = getRequiredElement<HTMLInputElement>(root, '#trend-min-epoch');
  const canvas = getRequiredElement<HTMLCanvasElement>(root, '#trend-chart');

  {
    const params = new URLSearchParams(window.location.search);
    const filterState = parseInternalsTrendsModelFilterState(params, allModelKeys);
    selectedModels = new Set(filterState.selectedModelKeys);
    modelSearch.value = filterState.search;
  }

  function transformY(value: number): number {
    return yScaleMode === 'symlog' ? symlog(value) : value;
  }

  function inverseY(value: number): number {
    return yScaleMode === 'symlog' ? symlogInverse(value) : value;
  }

  function buildCheckboxes(): void {
    const search = modelSearch.value.toLowerCase();
    checkboxContainer.innerHTML = '';

    for (const [key, name] of allModels) {
      if (search && !name.toLowerCase().includes(search)) {
        continue;
      }

      const label = document.createElement('label');
      label.className = 'model-checkbox';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = key;
      checkbox.checked = selectedModels.has(key);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          selectedModels.add(key);
        } else {
          selectedModels.delete(key);
        }
        updateChart();
        syncTrendsToUrl();
      });

      const colorIdx = allModels.findIndex(([candidate]) => candidate === key);
      const dot = document.createElement('span');
      dot.className = 'color-dot';
      dot.style.background = LINE_COLORS[colorIdx % LINE_COLORS.length];

      const span = document.createElement('span');
      span.textContent = name;

      label.appendChild(checkbox);
      label.appendChild(dot);
      label.appendChild(span);
      checkboxContainer.appendChild(label);
    }
  }

  let chart: Chart | null = null;

  function updateChart(): void {
    const metric = METRICS[currentMetric];
    if (!metric) {
      throw new Error(`Unknown trend metric: ${currentMetric}`);
    }

    const filteredGames = trendData.games
      .filter((game) => {
        if (game.epoch < minEpochTrend) {
          return false;
        }
        if (currentFormat !== 'all' && game.format !== currentFormat) {
          return false;
        }
        return game.ts !== '';
      })
      .sort((left, right) => new Date(left.ts).getTime() - new Date(right.ts).getTime());

    if (filteredGames.length === 0) {
      noteEl.textContent = 'No games for the selected filters.';
      if (chart) {
        chart.destroy();
        chart = null;
      }
      return;
    }

    const modelPoints = new Map<string, TrendPoint[]>();
    for (const game of filteredGames) {
      const ts = new Date(game.ts).getTime();
      if (!Number.isFinite(ts)) {
        continue;
      }

      for (const player of game.players) {
        if (!selectedModels.has(player.key)) {
          continue;
        }

        const rawValue = metric.compute(player);
        if (rawValue === null) {
          continue;
        }

        if (!modelPoints.has(player.key)) {
          modelPoints.set(player.key, []);
        }
        modelPoints.get(player.key)!.push({
          x: ts,
          y: transformY(rawValue),
          rawY: rawValue,
          modelName: player.modelName,
          modelCount: 0,
          format: game.format,
          gameId: game.id,
          dateLabel: formatDateTimeLabel(ts),
        });
      }
    }

    for (const points of modelPoints.values()) {
      points.sort((left, right) => left.x - right.x);
      for (const point of points) {
        point.modelCount = points.length;
      }
    }

    const datasets: ChartDataset<'scatter', TrendPoint[]>[] = [];
    const observedTimes: number[] = [];
    let totalObservations = 0;
    let modelsWithData = 0;

    for (const [modelKey] of allModels) {
      if (!selectedModels.has(modelKey)) {
        continue;
      }

      const points = modelPoints.get(modelKey);
      if (!points || points.length === 0) {
        continue;
      }

      modelsWithData += 1;
      totalObservations += points.length;
      observedTimes.push(...points.map((point) => point.x));

      const colorIdx = allModels.findIndex(([candidate]) => candidate === modelKey);
      const color = LINE_COLORS[colorIdx % LINE_COLORS.length];
      const modelName = modelMap.get(modelKey) ?? modelKey;

      datasets.push({
        label: `${modelName} (n=${points.length})`,
        data: points,
        type: 'scatter',
        borderColor: color,
        backgroundColor: withAlpha(color, '66'),
        pointRadius: 2.75,
        pointHoverRadius: 6,
        showLine: false,
      });
    }

    if (totalObservations === 0 || observedTimes.length === 0) {
      noteEl.textContent = 'No data for the selected filters. Try selecting different models or adjusting filters.';
      if (chart) {
        chart.destroy();
        chart = null;
      }
      return;
    }

    const minObserved = Math.min(...observedTimes);
    const maxObserved = Math.max(...observedTimes);
    const rangeMs = Math.max(maxObserved - minObserved, 60 * 60 * 1000);
    const xPadding = Math.max(rangeMs * 0.03, 30 * 60 * 1000);
    const xMin = minObserved - xPadding;
    const xMax = maxObserved + xPadding;

    const noteParts = [
      `${modelsWithData} model(s)`,
      `${totalObservations} observation(s)`,
      'raw points only',
      'x-axis uses actual timestamps',
    ];
    if (yScaleMode === 'symlog') {
      noteParts.push('y-axis uses symlog scaling');
    }
    noteEl.textContent = noteParts.join(' | ');

    const isPercentMetric = metric.unit === '%';

    if (chart) {
      chart.destroy();
    }
    chart = new Chart(canvas, {
      type: 'scatter',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'nearest',
          intersect: false,
        },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: '#9e9eab',
              boxWidth: 12,
              padding: 12,
              font: { size: 11 },
            },
          },
          tooltip: {
            backgroundColor: '#17171c',
            borderColor: '#2e2e38',
            borderWidth: 1,
            titleColor: '#f0f0f2',
            bodyColor: '#9e9eab',
            callbacks: {
              title: (items) => {
                if (items.length === 0) {
                  return '';
                }
                const point = items[0].raw as TrendPoint;
                return point.dateLabel;
              },
              label: (ctx) => {
                const point = ctx.raw as TrendPoint;
                return [
                  `${point.modelName}: ${formatMetricValue(point.rawY, metric.unit)}`,
                  `model observations in filter: n=${point.modelCount}`,
                  `format: ${point.format}`,
                  `game: ${point.gameId}`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: xMin,
            max: xMax,
            title: {
              display: true,
              text: 'Observed Time',
              color: '#9e9eab',
              font: { size: 11 },
            },
            ticks: {
              color: '#9e9eab',
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 8,
              font: { size: 11 },
              callback: (value) => formatTimeTick(Number(value), rangeMs),
            },
            grid: { color: 'rgba(255,255,255,0.1)' },
          },
          y: {
            min: isPercentMetric ? transformY(0) : undefined,
            max: isPercentMetric ? transformY(100) : undefined,
            title: {
              display: true,
              text: yScaleMode === 'symlog'
                ? `${metric.label}${metric.unit ? ` (${metric.unit})` : ''} [symlog]`
                : `${metric.label}${metric.unit ? ` (${metric.unit})` : ''}`,
              color: '#9e9eab',
              font: { size: 11 },
            },
            ticks: {
              color: '#9e9eab',
              font: { size: 11 },
              maxTicksLimit: 8,
              callback: (value) => formatMetricValue(inverseY(Number(value)), metric.unit),
            },
            grid: { color: 'rgba(255,255,255,0.1)' },
          },
        },
      },
    });
  }

  function syncTrendsToUrl(): void {
    if (controller.getActiveTabName() !== 'trends') {
      return;
    }

    controller.replaceUrlForTab('trends', (url) => {
      if (currentMetric !== 'costPerGame') {
        url.searchParams.set('metric', currentMetric);
      } else {
        url.searchParams.delete('metric');
      }

      if (currentFormat !== 'all') {
        url.searchParams.set('format', currentFormat);
      } else {
        url.searchParams.delete('format');
      }

      if (yScaleMode !== 'linear') {
        url.searchParams.set('yscale', yScaleMode);
      } else {
        url.searchParams.delete('yscale');
      }

      if (minEpochTrend !== 1) {
        url.searchParams.set('minEpoch', String(minEpochTrend));
      } else {
        url.searchParams.delete('minEpoch');
      }

      syncInternalsTrendsModelFilterStateToUrl(url, {
        selectedModelKeys: Array.from(selectedModels),
        search: modelSearch.value,
      }, allModelKeys);
      url.searchParams.delete('display');
      url.searchParams.delete('bucket');
      url.searchParams.delete('smoothing');
    });
  }

  controller.registerTabUrlSyncHandler('trends', syncTrendsToUrl);

  buildCheckboxes();
  modelSearch.addEventListener('input', () => {
    buildCheckboxes();
    syncTrendsToUrl();
  });

  getRequiredElement<HTMLButtonElement>(root, '#select-all-models').addEventListener('click', () => {
    selectedModels = new Set(allModelKeys);
    buildCheckboxes();
    updateChart();
    syncTrendsToUrl();
  });

  getRequiredElement<HTMLButtonElement>(root, '#select-none-models').addEventListener('click', () => {
    selectedModels.clear();
    buildCheckboxes();
    updateChart();
    syncTrendsToUrl();
  });

  root.querySelectorAll<HTMLElement>('#metric-buttons .metric-btn').forEach((button) => {
    button.addEventListener('click', () => {
      root.querySelectorAll('#metric-buttons .metric-btn').forEach((candidate) => {
        candidate.classList.remove('active');
      });
      button.classList.add('active');
      currentMetric = button.dataset.metric!;
      updateChart();
      syncTrendsToUrl();
    });
  });

  root.querySelectorAll<HTMLElement>('#format-buttons .toggle-btn').forEach((button) => {
    button.addEventListener('click', () => {
      root.querySelectorAll('#format-buttons .toggle-btn').forEach((candidate) => {
        candidate.classList.remove('active');
      });
      button.classList.add('active');
      currentFormat = button.dataset.format!;
      updateChart();
      syncTrendsToUrl();
    });
  });

  root.querySelectorAll<HTMLElement>('#y-scale-buttons .toggle-btn').forEach((button) => {
    button.addEventListener('click', () => {
      root.querySelectorAll('#y-scale-buttons .toggle-btn').forEach((candidate) => {
        candidate.classList.remove('active');
      });
      button.classList.add('active');
      yScaleMode = button.dataset.scale as YScaleMode;
      updateChart();
      syncTrendsToUrl();
    });
  });

  minEpochTrendInput.addEventListener('input', () => {
    minEpochTrend = parseInt(minEpochTrendInput.value, 10) || 1;
    updateChart();
    syncTrendsToUrl();
  });

  {
    const params = new URLSearchParams(window.location.search);
    const metricParam = params.get('metric');
    if (metricParam && METRICS[metricParam]) {
      currentMetric = metricParam;
      setActiveButton(root, '#metric-buttons .metric-btn', 'metric', metricParam);
    }

    const formatParam = params.get('format');
    if (formatParam && root.querySelector(`#format-buttons .toggle-btn[data-format="${formatParam}"]`)) {
      currentFormat = formatParam;
      setActiveButton(root, '#format-buttons .toggle-btn', 'format', formatParam);
    }

    const yScaleParam = params.get('yscale');
    if (yScaleParam === 'linear' || yScaleParam === 'symlog') {
      yScaleMode = yScaleParam;
      setActiveButton(root, '#y-scale-buttons .toggle-btn', 'scale', yScaleParam);
    }

    const minEpochParam = params.get('minEpoch');
    if (minEpochParam !== null) {
      const value = parseInt(minEpochParam, 10);
      if (!Number.isNaN(value) && value >= 1) {
        minEpochTrend = value;
        minEpochTrendInput.value = String(value);
      }
    }
  }

  syncTrendsToUrl();
  updateChart();
}
