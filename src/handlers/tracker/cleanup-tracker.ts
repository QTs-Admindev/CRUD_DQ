import { getDbConnection } from "../../lib/db";
import { update, deleteRecord, deleteWhere } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { TrackerEvent } from "../../lib/event-types";

/**
 * Cleanup Tracker — ATOMIC unbind + optional delete.
 *
 * Combines what used to be 3 separate Lambdas:
 * 1. Unbind Vehicle Tracker (remove tbox from vehicle in SmartTyre)
 * 2. Update MySQL (clear trucks.tbox_id)
 * 3. Delete Tracker (remove tbox record + mapping if needed)
 *
 * All in a single MySQL transaction. If any step fails, everything rolls back.
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[CleanupTracker] Input:", JSON.stringify(event));

  const {
    vehicleId,
    currentTboxId,
    currentSmartTyreVehicleId,
    needsDelete,
  } = event;

  if (!currentSmartTyreVehicleId) {
    throw new Error("currentSmartTyreVehicleId is required for unbind");
  }

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    // 1. Unbind in SmartTyre — remove tbox from vehicle
    const unbindResp: any = await SmartTyreService.updateVehicle(
      currentSmartTyreVehicleId,
      { tboxCode: null } as any
    );
    console.log("[CleanupTracker] SmartTyre unbind response:", unbindResp);

    // 2. Clear tbox reference in MySQL
    await update(db, "trucks", vehicleId, { tbox_id: null });
    event.actionsPerformed = event.actionsPerformed || [];
    event.actionsPerformed.push("tracker_unbound");

    // 3. If delete requested, remove the tbox record and its mapping
    if (needsDelete && currentTboxId) {
      await deleteRecord(db, "tboxes", currentTboxId);
      await deleteWhere(db, "tbox_id_mapping", { quinta_id: currentTboxId });
      event.actionsPerformed.push("tracker_deleted");
      console.log(`[CleanupTracker] Deleted tbox ${currentTboxId} and its mapping`);
    }

    await db.commit();
    console.log("[CleanupTracker] Cleanup completed successfully");
    return event;
  } catch (error) {
    await db.rollback();
    console.error("[CleanupTracker] Error, rolled back:", error);
    throw error;
  } finally {
    await db.end();
  }
};
