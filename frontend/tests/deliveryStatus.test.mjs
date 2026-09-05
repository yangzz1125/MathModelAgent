import test from 'node:test';
import assert from 'node:assert/strict';
import { isAcceptedDelivery } from '../src/utils/deliveryStatus.ts';

test('accepted deliveries include explicit nonfatal warnings', () => {
  assert.equal(isAcceptedDelivery('completed'), true);
  assert.equal(isAcceptedDelivery('completed_with_warnings'), true);
});
test('partial and unverified work never shows as accepted', () => {
  for (const status of ['partial', 'failed', 'running', 'starting', 'paused', 'cancelled', undefined]) {
    assert.equal(isAcceptedDelivery(status), false, String(status));
  }
});
