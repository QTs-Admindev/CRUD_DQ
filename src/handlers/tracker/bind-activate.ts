import { getDbConnection } from "../../lib/db";
import { update } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { TrackerEvent } from "../../lib/event-types";

/**
 * Bind & Activate — ATOMIC bind tracker to vehicle + set active status.
 *
 * Order: bind FIRST, then activate.
 * A tracker should never be "active" without being bound to a vehicle.
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[BindActivate] Input:", JSON.stringify(event));

  const {
    vehicleId,
    currentSmartTyreVehicleId,
    newTboxId,
    newTrackerCode,
  } = event;

  if (!currentSmartTyreVehicleId) {
    throw new Error("currentSmartTyreVehicleId is required");
  }
  if (!newTboxId || !newTrackerCode) {
    throw new Error("newTboxId and newTrackerCode are required");
  }

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    // 1. Bind in SmartTyre — assign tbox to vehicle
    const bindResp: any = await SmartTyreService.updateVehicle(
      currentSmartTyreVehicleId,
      { tboxCode: newTrackerCode } as any
    );
    console.log("[BindActivate] SmartTyre bind response:", bindResp);

    // 2. Bind in MySQL — set tbox_id on the truck
    await update(db, "trucks", vehicleId, { tbox_id: newTboxId });

    // 3. Activate — set tbox status to active (AFTER bind, not before)
    await update(db, "tboxes", newTboxId, { status: "active" });

    await db.commit();

    event.actionsPerformed = event.actionsPerformed || [];
    event.actionsPerformed.push("tracker_bound", "tracker_activated");

    console.log(
      `[BindActivate] ✅ TBox ${newTrackerCode} bound to vehicle ${vehicleId} and activated`
    );
    return event;
  } catch (error) {
    await db.rollback();
    console.error("[BindActivate] Error, rolled back:", error);
    throw error;
  } finally {
    await db.end();
  }
};
