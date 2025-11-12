export const rollThresholds = {
  minCredit: 0.2,
  minPopShift: 1.5,
};

export const spreadsScanThresholds = {
  dteTarget: 10,
  dtePenaltyDivisor: 30,
  statusBonus: 0.05,
  minOpenInterest: 50,
  maxBidAskPct: 25,
  idealDelta: 0.15,
  deltaTolerance: 0.05,
  oiBonus: 0.08,
  baPenaltyWeight: 0.4,
  deltaPenaltyWeight: 0.6,
  ivrWeight: 0.1,
};

export type RollThresholds = typeof rollThresholds;
export type SpreadsScanThresholds = typeof spreadsScanThresholds;
