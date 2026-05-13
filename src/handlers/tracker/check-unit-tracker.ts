import { getDbConnection } from "../../lib/db";
import { getById, findSmartTyreId } from "../../lib/db-operations";
import { TrackerEvent } from "../../lib/event-types";
import { RowDataPacket } from "mysql2/promise";

/**
 * Check Unit Tracker — READ-ONLY step.
 *
 * Determines what actions are needed based on the vehicle's current tracker state
 * and the requested new tracker code.
 *
 * Decision matrix:
 * ┌──────────────────────┬──────────────┬─────────────────────────────────────────┐
 * │ Current State        │ New Code     │ Actions                                 │
 * ├──────────────────────┼──────────────┼─────────────────────────────────────────┤
 * │ No tracker           │ provided     │ needsCreate = true                      │
 * │ Has tracker, same    │ same code    │ (no action needed)                      │
 * │ Has tracker, changed │ diff code    │ needsUnbind + needsDelete + needsCreate │
 * │ Has tracker          │ empty/null   │ needsUnbind + needsDelete               │
 * │ Has tracker, edit    │ diff code*   │ needsEdit = true (TBD business rule)    │
 * └──────────────────────┴──────────────┴─────────────────────────────────────────┘
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[CheckUnitTracker] Input:", JSON.stringify(event));

  const { vehicleId, newTrackerCode } = event;

  if (!vehicleId) throw new Error("vehicleId is required");

  const db = await getDbConnection();

  try {
    // 1. Get the vehicle's current tbox assignment
    const vehicle = await getById(db, "trucks", vehicleId) as RowDataPacket | null;
    if (!vehicle) throw new Error(`Vehicle ${vehicleId} not found`);

    const currentTboxId: number | null = vehicle.tbox_id ?? null;
    let currentTrackerCode: string | null = null;
    let currentSmartTyreVehicleId: string | null = null;

    // 2. If vehicle has a tbox, get its code
    if (currentTboxId) {
      const tbox = await getById(db, "tboxes", currentTboxId) as RowDataPacket | null;
      currentTrackerCode = tbox?.tboxCode ?? null;
    }

    // 3. Get the SmartTyre vehicle ID for API calls
    currentSmartTyreVehicleId = await findSmartTyreId(
      db,
      "truck_id_mapping",
      vehicleId
    );

    // 4. Determine actions
    const hasTracker = !!currentTboxId && !!currentTrackerCode;
    const trackerChanged =
      hasTracker && !!newTrackerCode && currentTrackerCode !== newTrackerCode;
    const trackerRemoved = hasTracker && !newTrackerCode;
    const needsNewTracker = !hasTracker && !!newTrackerCode;

    const result: TrackerEvent = {
      ...event,
      currentTrackerCode,
      currentTboxId,
      currentSmartTyreVehicleId,
      needsUnbind: trackerChanged || trackerRemoved,
      needsDelete: trackerChanged || trackerRemoved,
      needsCreate: trackerChanged || needsNewTracker,
      needsEdit: false, // Edit only if the tbox exists but code format needs update
      actionsPerformed: [],
    };

    console.log("[CheckUnitTracker] Decision:", {
      hasTracker,
      trackerChanged,
      trackerRemoved,
      needsNewTracker,
      ...result,
    });

    return result;
  } finally {
    await db.end();
  }
};
