import { differenceInCalendarDays } from 'date-fns';

export function dteFromISO(expirationISO: string): number {
  const today = new Date();
  const exp = new Date(expirationISO);
  const diff = differenceInCalendarDays(exp, today);
  return Math.max(0, diff);
}

export function isIC(strategyType: string): boolean {
  return strategyType.toUpperCase() === 'IC' || strategyType.toUpperCase() === 'IRON_CONDOR';
}
