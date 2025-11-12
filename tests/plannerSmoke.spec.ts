import test from 'node:test';
import assert from 'node:assert/strict';

import { parsePlannerResponse } from '@stratdeck/mcp-server/planner';

const sampleResponse = `Summary: Recommend reviewing IC_SPY.

ActionPlan {"summary":"Review IC_SPY","actionPlan":{"toolCalls":[{"name":"spreads.scan","arguments":{"type":"IC"},"status":"completed"}],"humanFollowUp":[]}}`;

test('planner response parsing extracts action plan JSON', () => {
  const result = parsePlannerResponse(sampleResponse);
  assert.equal(result.actionPlan.toolCalls.length, 1);
  assert.equal(result.actionPlan.toolCalls[0].name, 'spreads.scan');
});
