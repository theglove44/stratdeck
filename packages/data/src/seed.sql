INSERT OR REPLACE INTO strategies (id, underlying, strategy_type, status, opened_at)
VALUES
  ('IC_SPY_2025-10-29', 'SPY', 'IC', 'OPEN', date('now','-5 days')),
  ('IC_XSP_2025-11-18', 'XSP', 'IC', 'OPEN', date('now','-1 days')),
  ('PCS_QQQ_2025-11-18', 'QQQ', 'PCS', 'OPEN', date('now','-2 days')),
  ('CCS_GOOGL_2025-10-29', 'GOOGL', 'CCS', 'OPEN', date('now','-3 days'));

INSERT OR REPLACE INTO legs (id, strategy_id, side, call_put, strike, expiration, qty, avg_price, opened_at) VALUES
  -- SPY IC (8 DTE)
  ('L1', 'IC_SPY_2025-10-29', 'SHORT', 'CALL',  560.0, '2025-10-29', 1, 0.55, date('now','-5 days')),
  ('L2', 'IC_SPY_2025-10-29', 'LONG',  'CALL',  565.0, '2025-10-29', 1, -0.25, date('now','-5 days')),
  ('L3', 'IC_SPY_2025-10-29', 'SHORT', 'PUT',   520.0, '2025-10-29', 1, 0.60, date('now','-5 days')),
  ('L4', 'IC_SPY_2025-10-29', 'LONG',  'PUT',   515.0, '2025-10-29', 1, -0.30, date('now','-5 days')),

  -- XSP IC (28 DTE)
  ('L5', 'IC_XSP_2025-11-18', 'SHORT', 'CALL',   595.0, '2025-11-18',  1, 0.45, date('now','-1 days')),
  ('L6', 'IC_XSP_2025-11-18', 'LONG',  'CALL',   600.0, '2025-11-18',  1, -0.20, date('now','-1 days')),
  ('L7', 'IC_XSP_2025-11-18', 'SHORT', 'PUT',    530.0, '2025-11-18',  1, 0.50, date('now','-1 days')),
  ('L8', 'IC_XSP_2025-11-18', 'LONG',  'PUT',    525.0, '2025-11-18',  1, -0.22, date('now','-1 days')),

  -- QQQ PCS (28 DTE)
  ('L9', 'PCS_QQQ_2025-11-18', 'SHORT', 'PUT',   460.0, '2025-11-18',  1, 0.75, date('now','-2 days')),
  ('L10','PCS_QQQ_2025-11-18', 'LONG',  'PUT',   455.0, '2025-11-18',  1, -0.40, date('now','-2 days')),

  -- GOOGL CCS (8 DTE)
  ('L11','CCS_GOOGL_2025-10-29', 'SHORT', 'CALL',  180.0, '2025-10-29', 1, 0.62, date('now','-3 days')),
  ('L12','CCS_GOOGL_2025-10-29', 'LONG',  'CALL',  182.0, '2025-10-29', 1, -0.38, date('now','-3 days'));
