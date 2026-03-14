import { Chart, registerables } from 'chart.js';

import type { BlunderInternalsData, BlunderRun } from '../../utils/internals-types';
import type { InternalsDashboardController } from './dashboard-controller';
import { getRequiredElement } from './dom';
import { fetchJson } from './fetch-json';

Chart.register(...registerables);

const BLUNDER_METRICS: Record<string, {
  label: string;
  unit: string;
  compute: (_run: BlunderRun) => number | null;
}> = {
  cacheRate: {
    label: 'Cache Rate',
    unit: '%',
    compute: (run) => run.promptTokens > 0 ? (run.cachedTokens / run.promptTokens) * 100 : null,
  },
  cost: {
    label: 'Cost',
    unit: '$',
    compute: (run) => run.costUsd,
  },
  promptTokens: {
    label: 'Prompt Tokens',
    unit: '',
    compute: (run) => run.promptTokens,
  },
  decisions: {
    label: 'Decisions Analyzed',
    unit: '',
    compute: (run) => run.decisionsAnalyzed,
  },
};

export async function initInternalsBlunderTab(options: {
  controller: InternalsDashboardController;
  dataUrl: string;
  root: HTMLElement;
}): Promise<void> {
  const { controller, dataUrl, root } = options;
  const note = getRequiredElement<HTMLElement>(root, '#blunder-chart-note');

  let blunderRaw: BlunderInternalsData;
  try {
    blunderRaw = await fetchJson<BlunderInternalsData>(dataUrl);
  } catch (error) {
    note.textContent = error instanceof Error ? error.message : 'Failed to load blunder analysis.';
    throw error;
  }

  let blunderMetric = 'cacheRate';
  let blunderMinVersion = 1;
  let blunderChart: Chart | null = null;

  const blunderCanvas = getRequiredElement<HTMLCanvasElement>(root, '#blunder-chart');
  const blunderMinVersionInput = getRequiredElement<HTMLInputElement>(root, '#blunder-min-version');

  function syncBlunderToUrl(): void {
    if (controller.getActiveTabName() !== 'blunder') {
      return;
    }

    controller.replaceUrlForTab('blunder', (url) => {
      if (blunderMetric !== 'cacheRate') {
        url.searchParams.set('bmetric', blunderMetric);
      } else {
        url.searchParams.delete('bmetric');
      }

      if (blunderMinVersion !== 1) {
        url.searchParams.set('bminVer', String(blunderMinVersion));
      } else {
        url.searchParams.delete('bminVer');
      }
    });
  }

  controller.registerTabUrlSyncHandler('blunder', syncBlunderToUrl);

  function updateBlunderChart(): void {
    const metric = BLUNDER_METRICS[blunderMetric];
    if (!metric) {
      throw new Error(`Unknown blunder metric: ${blunderMetric}`);
    }

    const filtered = blunderRaw.runs
      .filter((run) => run.version >= blunderMinVersion && run.ts)
      .sort((left, right) => left.ts.localeCompare(right.ts));

    const points: Array<{ x: number; y: number; label: string }> = [];
    filtered.forEach((run, index) => {
      const value = metric.compute(run);
      if (value === null) {
        return;
      }

      const date = new Date(run.ts);
      const dateLabel = `${date.getMonth() + 1}/${date.getDate()}`;
      points.push({
        x: index,
        y: value,
        label: `${run.gameId}\n${dateLabel} v${run.version}`,
      });
    });

    const dateLabels = new Map<number, Date>();
    filtered.forEach((run, index) => {
      dateLabels.set(index, new Date(run.ts));
    });

    note.textContent = `${filtered.length} annotation run(s)`;

    if (blunderChart) {
      blunderChart.destroy();
    }

    blunderChart = new Chart(blunderCanvas, {
      type: 'scatter',
      data: {
        datasets: [{
          label: metric.label,
          data: points,
          backgroundColor: 'rgba(233, 69, 96, 0.6)',
          borderColor: '#e94560',
          pointRadius: 4,
          pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const point = ctx.raw as { x: number; y: number; label: string };
                const value = metric.unit === '%'
                  ? `${point.y.toFixed(1)}%`
                  : metric.unit === '$'
                    ? `$${point.y.toFixed(3)}`
                    : point.y.toLocaleString();
                return `${point.label}: ${value}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            title: { display: true, text: 'Annotation Run', color: '#9e9eab' },
            ticks: {
              color: '#9e9eab',
              callback: (value) => {
                const index = typeof value === 'string' ? parseInt(value, 10) : value;
                const date = dateLabels.get(index);
                return date ? `${date.getMonth() + 1}/${date.getDate()}` : '';
              },
            },
            grid: { color: 'rgba(255,255,255,0.1)' },
          },
          y: {
            title: {
              display: true,
              text: metric.unit ? `${metric.label} (${metric.unit})` : metric.label,
              color: '#9e9eab',
            },
            ticks: { color: '#9e9eab' },
            grid: { color: 'rgba(255,255,255,0.1)' },
          },
        },
      },
    });
  }

  root.querySelectorAll<HTMLElement>('#blunder-metric-buttons .metric-btn').forEach((button) => {
    button.addEventListener('click', () => {
      root.querySelectorAll('#blunder-metric-buttons .metric-btn').forEach((candidate) => {
        candidate.classList.remove('active');
      });
      button.classList.add('active');
      blunderMetric = button.dataset.metric!;
      updateBlunderChart();
      syncBlunderToUrl();
    });
  });

  blunderMinVersionInput.addEventListener('input', () => {
    blunderMinVersion = parseInt(blunderMinVersionInput.value, 10) || 1;
    updateBlunderChart();
    syncBlunderToUrl();
  });

  {
    const params = new URLSearchParams(window.location.search);
    const metric = params.get('bmetric');
    if (metric && BLUNDER_METRICS[metric]) {
      blunderMetric = metric;
      root.querySelectorAll('#blunder-metric-buttons .metric-btn').forEach((candidate) => {
        candidate.classList.remove('active');
      });
      root.querySelector<HTMLElement>(`#blunder-metric-buttons .metric-btn[data-metric="${metric}"]`)?.classList.add('active');
    }

    const minVersion = params.get('bminVer');
    if (minVersion !== null) {
      const value = parseInt(minVersion, 10);
      if (!Number.isNaN(value) && value >= 1) {
        blunderMinVersion = value;
        blunderMinVersionInput.value = String(value);
      }
    }
  }

  syncBlunderToUrl();
  updateBlunderChart();
}
