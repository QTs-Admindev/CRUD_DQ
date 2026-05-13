// ═══════════════════════════════════════════════════════════════════════════════
// Step Functions Event Types — shared across all handlers
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Base event shape passed through the asset creation Step Functions workflow.
 */
export interface AssetEvent {
  jobId?: string;
  companyId: number;
  resourceName: string;
  stage?: string;
  payload: Record<string, any>;
}

/**
 * Event shape for the tracker update Step Functions workflow.
 */
export interface TrackerEvent {
  vehicleId: number;
  companyId: number;
  /** The new TBox code to assign (or undefined if removing) */
  newTrackerCode?: string;
  /** The new vehicle type to set (0=car, 1=tractor, 2=trailer) */
  newVehicleType?: 0 | 1 | 2;

  // ── Decision flags (set by Check Unit Tracker) ──
  currentTrackerCode?: string | null;
  currentTboxId?: number | null;
  currentSmartTyreVehicleId?: string | null;
  needsUnbind?: boolean;
  needsDelete?: boolean;
  needsCreate?: boolean;
  needsEdit?: boolean;

  // ── Results (populated by downstream Lambdas) ──
  newTboxId?: number;
  newSmartTyreTboxId?: number;
  actionsPerformed?: string[];
  error?: string;
}
