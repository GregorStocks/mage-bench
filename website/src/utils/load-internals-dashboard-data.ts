import fs from 'node:fs';
import path from 'node:path';

import blunderInternalsData from '../data/blunder-internals.json';
import internalsTrendData from '../data/internals-data.json';
import modelStatsData from '../data/model-stats.json';
import type {
  BlunderInternalsData,
  GoldenTestScenario,
  InternalsTrendData,
  ModelStatsData,
} from './internals-types';

const GOLDEN_EXPORTS_DIR = path.resolve(
  process.cwd(),
  '..',
  'puppeteer',
  'tests',
  'golden',
  'exports',
);

interface GoldenExport {
  snapshots?: Array<{ turn?: number }>;
}

export function loadInternalsTrendData(): InternalsTrendData {
  return internalsTrendData as InternalsTrendData;
}

export function loadModelStatsData(): ModelStatsData {
  return modelStatsData as ModelStatsData;
}

export function loadBlunderInternalsData(): BlunderInternalsData {
  return blunderInternalsData as BlunderInternalsData;
}

export function loadGoldenTestScenarios(exportsDir: string = GOLDEN_EXPORTS_DIR): GoldenTestScenario[] {
  if (!fs.existsSync(exportsDir)) {
    throw new Error(`Missing golden exports directory: ${exportsDir}`);
  }

  return fs.readdirSync(exportsDir).sort().flatMap((file) => {
    if (!file.endsWith('.json')) {
      return [];
    }

    const filePath = path.join(exportsDir, file);
    const data = JSON.parse(fs.readFileSync(filePath, 'utf-8')) as GoldenExport;
    const snapshots = data.snapshots ?? [];
    const lastSnapshot = snapshots.at(-1);
    if (lastSnapshot != null && typeof lastSnapshot.turn !== 'number') {
      throw new Error(`Golden export is missing a numeric final turn: ${filePath}`);
    }

    return [{
      name: file.replace(/\.json$/, ''),
      snapshots: snapshots.length,
      turns: lastSnapshot?.turn ?? 0,
    }];
  });
}
