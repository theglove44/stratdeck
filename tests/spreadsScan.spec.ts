import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';

import type { SpreadRow, SpreadLegRow } from '@stratdeck/data';
import { rankSpreads } from '@stratdeck/mcp-server/handlers/spreadsScan';
import { clearLiveCache, subscribeLegs, getSpreadLiveSummary, evaluateSpreadMetrics } from '@stratdeck/data-live';
import { spreadsScanThresholds } from '@stratdeck/config';

const spyLegs: SpreadLegRow[] = [
  { id: 'L1', side: 'SHORT', call_put: 'CALL', strike: 560, expiration: '2025-10-29', qty: 1 },
  { id: 'L2', side: 'LONG', call_put: 'CALL', strike: 565, expiration: '2025-10-29', qty: 1 },
  { id: 'L3', side: 'SHORT', call_put: 'PUT', strike: 520, expiration: '2025-10-29', qty: 1 },
  { id: 'L4', side: 'LONG', call_put: 'PUT', strike: 515, expiration: '2025-10-29', qty: 1 },
];

const xspLegs: SpreadLegRow[] = [
  { id: 'L5', side: 'SHORT', call_put: 'CALL', strike: 595, expiration: '2025-11-18', qty: 1 },
  { id: 'L6', side: 'LONG', call_put: 'CALL', strike: 600, expiration: '2025-11-18', qty: 1 },
  { id: 'L7', side: 'SHORT', call_put: 'PUT', strike: 530, expiration: '2025-11-18', qty: 1 },
  { id: 'L8', side: 'LONG', call_put: 'PUT', strike: 525, expiration: '2025-11-18', qty: 1 },
];

const mockSpreads: SpreadRow[] = [
  {
    strategy_id: 'IC_SPY_2025-10-29',
    underlying: 'SPY',
    strategy_type: 'IC',
    status: 'OPEN',
    expiration: '2025-10-29',
    credit_received: 0.65,
    width: 5,
    legs: spyLegs,
  },
  {
    strategy_id: 'IC_XSP_2025-11-18',
    underlying: 'XSP',
    strategy_type: 'IC',
    status: 'OPEN',
    expiration: '2025-11-18',
    credit_received: 0.45,
    width: 5,
    legs: xspLegs,
  },
];

test('spreads.scan ranks IC spreads with live metrics', () => {
  clearLiveCache();
  subscribeLegs([
    { symbol: 'SPY251029C00560000' },
    { symbol: 'SPY251029C00565000' },
    { symbol: 'SPY251029P00520000' },
    { symbol: 'SPY251029P00515000' },
  ], { fixturePath: resolve('fixtures/live_quotes_sample.json') });

  const prepared = mockSpreads.map((spread) => {
    const dte = spread.strategy_id === 'IC_SPY_2025-10-29' ? 9 : 28;
    const live = getSpreadLiveSummary(spread.underlying, spread.legs);
    const metrics = live
      ? evaluateSpreadMetrics(live, {
          minOpenInterest: spreadsScanThresholds.minOpenInterest,
          maxBidAskPct: spreadsScanThresholds.maxBidAskPct,
          idealDelta: spreadsScanThresholds.idealDelta,
          deltaTolerance: spreadsScanThresholds.deltaTolerance,
        })
      : { baPct: spreadsScanThresholds.maxBidAskPct * 2, oiOk: false };
    return { ...spread, dte, live, ...metrics } as any;
  });

  const ranked = rankSpreads(prepared);
  assert.equal(ranked[0].strategy_id, 'IC_SPY_2025-10-29');
  assert.equal(ranked[1].strategy_id, 'IC_XSP_2025-11-18');
  assert.ok(ranked[0].baPct !== undefined);
  assert.equal(ranked[0].oiOk, true);
});
