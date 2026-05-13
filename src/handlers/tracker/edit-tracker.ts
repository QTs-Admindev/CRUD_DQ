import { getDbConnection } from "../../lib/db";
import { update, findSmartTyreId } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { TrackerEvent } from "../../lib/event-types";

/**
 * Edit Tracker — updates the tbox code on an existing tracker.
 * Used when the tracker already exists and is bound, but its code needs changing.
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[EditTracker] Input:", JSON.stringify(event));

  const { currentTboxId, newTrackerCode } = event;

  if (!currentTboxId) throw new Error("currentTboxId is required");
  if (!newTrackerCode) throw new Error("newTrackerCode is required");

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    // 1. Get SmartTyre tbox ID
    const smartTyreId = await findSmartTyreId(
      db,
      "tbox_id_mapping",
      currentTboxId
    );
    if (!smartTyreId) {
      throw new Error(
        `No SmartTyre mapping found for tbox ${currentTboxId}`
      );
    }

    // 2. Update in SmartTyre
    const resp: any = await SmartTyreService.updateTbox(smartTyreId, {
      tboxCode: newTrackerCode,
    });
    console.log("[EditTracker] SmartTyre update response:", resp);

    // 3. Update in MySQL
    await update(db, "tboxes", currentTboxId, { tboxCode: newTrackerCode });

    await db.commit();

    event.actionsPerformed = event.actionsPerformed || [];
    event.actionsPerformed.push("tracker_edited");

    console.log(
      `[EditTracker] ✅ TBox ${currentTboxId} code updated to ${newTrackerCode}`
    );
    return event;
  } catch (error) {
    await db.rollback();
    console.error("[EditTracker] Error, rolled back:", error);
    throw error;
  } finally {
    await db.end();
  }
};
