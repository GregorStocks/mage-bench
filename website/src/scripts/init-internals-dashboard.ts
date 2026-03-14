import type { InternalsTabName } from '../utils/internals-tab-url-state';
import { createInternalsDashboardController } from './internals/dashboard-controller';
import { getRequiredElement } from './internals/dom';

export async function initInternalsDashboard(options?: { root?: HTMLElement }): Promise<void> {
  const root = options?.root ?? document.getElementById('internals-dashboard');
  if (!(root instanceof HTMLElement)) {
    return;
  }

  const controller = createInternalsDashboardController(root);
  const initializedTabs = new Set<InternalsTabName>();

  const tabInitializers: Partial<Record<InternalsTabName, () => Promise<void>>> = {
    trends: async () => {
      const panel = getRequiredElement<HTMLElement>(root, '#tab-trends');
      const dataUrl = panel.dataset.dataUrl;
      if (!dataUrl) {
        throw new Error('Missing trends data URL');
      }

      const { initInternalsTrendsTab } = await import('./internals/trends');
      await initInternalsTrendsTab({ controller, dataUrl, root: panel });
    },
    'model-stats': async () => {
      const panel = getRequiredElement<HTMLElement>(root, '#tab-model-stats');
      const dataUrl = panel.dataset.dataUrl;
      if (!dataUrl) {
        throw new Error('Missing model stats data URL');
      }

      const { initInternalsModelStatsTab } = await import('./internals/model-stats');
      await initInternalsModelStatsTab({ controller, dataUrl, root: panel });
    },
    blunder: async () => {
      const panel = getRequiredElement<HTMLElement>(root, '#tab-blunder');
      const dataUrl = panel.dataset.dataUrl;
      if (!dataUrl) {
        throw new Error('Missing blunder data URL');
      }

      const { initInternalsBlunderTab } = await import('./internals/blunder');
      await initInternalsBlunderTab({ controller, dataUrl, root: panel });
    },
  };

  async function ensureTabInitialized(tabName: InternalsTabName): Promise<void> {
    if (initializedTabs.has(tabName)) {
      return;
    }

    initializedTabs.add(tabName);
    const initializer = tabInitializers[tabName];
    if (initializer) {
      await initializer();
    }
  }

  async function activateTab(tabName: InternalsTabName): Promise<void> {
    controller.switchTab(tabName);
    await ensureTabInitialized(tabName);
    controller.syncActiveTabToUrl(tabName);
  }

  root.querySelectorAll<HTMLButtonElement>('.tab[data-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      const tabName = button.dataset.tab;
      if (tabName !== 'trends' && tabName !== 'model-stats' && tabName !== 'blunder' && tabName !== 'golden') {
        throw new Error(`Unexpected internals tab: ${tabName}`);
      }

      void activateTab(tabName);
    });
  });

  await activateTab(controller.readTabFromLocation());
  window.addEventListener('hashchange', () => {
    void activateTab(controller.readTabFromLocation());
  });
}
