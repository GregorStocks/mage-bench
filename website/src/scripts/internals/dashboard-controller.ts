import {
  isInternalsTabName,
  syncInternalsTabToUrl,
  type InternalsTabName,
} from '../../utils/internals-tab-url-state';
import { getRequiredElement } from './dom';

const TAB_NAMES: InternalsTabName[] = ['trends', 'model-stats', 'blunder', 'golden'];

export interface InternalsDashboardController {
  getActiveTabName(): InternalsTabName;
  readTabFromLocation(): InternalsTabName;
  registerTabUrlSyncHandler(_tabName: InternalsTabName, _handler: () => void): void;
  replaceUrlForTab(_tabName: InternalsTabName, _updateUrl?: (_url: URL) => void): void;
  switchTab(_tabName: InternalsTabName): void;
  syncActiveTabToUrl(_tabName: InternalsTabName): void;
}

export function createInternalsDashboardController(root: HTMLElement): InternalsDashboardController {
  const buttons = new Map<InternalsTabName, HTMLButtonElement>();
  const panels = new Map<InternalsTabName, HTMLElement>();
  const urlSyncHandlers = new Map<InternalsTabName, () => void>();

  for (const tabName of TAB_NAMES) {
    buttons.set(
      tabName,
      getRequiredElement<HTMLButtonElement>(root, `.tab[data-tab="${tabName}"]`),
    );
    panels.set(
      tabName,
      getRequiredElement<HTMLElement>(root, `#tab-${tabName}`),
    );
  }

  function switchTab(tabName: InternalsTabName): void {
    for (const candidate of TAB_NAMES) {
      buttons.get(candidate)!.classList.toggle('active', candidate === tabName);
      panels.get(candidate)!.classList.toggle('active', candidate === tabName);
    }
  }

  function getActiveTabName(): InternalsTabName {
    const activeTabName = root.querySelector<HTMLElement>('.tab.active')?.dataset.tab;
    if (!activeTabName || !isInternalsTabName(activeTabName)) {
      return 'trends';
    }
    return activeTabName;
  }

  function readTabFromLocation(): InternalsTabName {
    const params = new URLSearchParams(window.location.search);
    const tabParam = params.get('tab');
    if (tabParam && isInternalsTabName(tabParam)) {
      return tabParam;
    }

    const hash = window.location.hash.replace(/^#/, '');
    if (isInternalsTabName(hash)) {
      return hash;
    }

    return 'trends';
  }

  function replaceUrlForTab(tabName: InternalsTabName, updateUrl?: (_url: URL) => void): void {
    const url = new URL(window.location.href);
    syncInternalsTabToUrl(url, tabName);
    updateUrl?.(url);
    history.replaceState(null, '', url.toString());
  }

  function syncActiveTabToUrl(tabName: InternalsTabName): void {
    const handler = urlSyncHandlers.get(tabName);
    if (handler) {
      handler();
      return;
    }
    replaceUrlForTab(tabName);
  }

  function registerTabUrlSyncHandler(tabName: InternalsTabName, handler: () => void): void {
    urlSyncHandlers.set(tabName, handler);
  }

  return {
    getActiveTabName,
    readTabFromLocation,
    registerTabUrlSyncHandler,
    replaceUrlForTab,
    switchTab,
    syncActiveTabToUrl,
  };
}
