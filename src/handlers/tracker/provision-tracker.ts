import { getDbConnection } from "../../lib/db";
import { create, createRelationRecord } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { TrackerEvent } from "../../lib/event-types";

const TBOX_CODE_REGEX = /^[0-9A-Fa-f]{12}$/;

/**
 * Provision Tracker — ATOMIC create + ID mapping.
 *
 * Combines what used to be 3 separate Lambdas:
 * 1. Create Tracker (insert in SmartTyre + MySQL)
 * 2. Tracker Mapping (lookup SmartTyre ID + insert mapping)
 * 3. (Activate was here but moved to bind-activate)
 *
 * All in a single MySQL transaction.
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[ProvisionTracker] Input:", JSON.stringify(event));

  const { companyId, newTrackerCode } = event;

  if (!newTrackerCode) throw new Error("newTrackerCode is required");
  if (!TBOX_CODE_REGEX.test(newTrackerCode)) {
    throw new Error(
      `Invalid tboxCode format: "${newTrackerCode}". Must be 12 hex characters (0-9, A-F).`
    );
  }

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    // 1. Create in SmartTyre proxy
    const createResp: any = await SmartTyreService.createTbox({
      tboxCode: newTrackerCode,
    });
    if (!createResp || createResp !== "Success") {
      throw new Error(
        `SmartTyre failed to create TBox: ${JSON.stringify(createResp)}`
      );
    }
    console.log("[ProvisionTracker] Created in SmartTyre");

    // 2. Create in MySQL
    const record = await create(db, "tboxes", {
      tboxCode: newTrackerCode,
      company_id: companyId,
      status: "registering",
    });
    const localTboxId = record.id;
    console.log(`[ProvisionTracker] MySQL tbox created: id=${localTboxId}`);

    // 3. Get SmartTyre ID via listing (mapping step)
    const listResp = await SmartTyreService.listTboxes({
      tboxCode: newTrackerCode,
    });
    if (!listResp?.records?.length) {
      throw new Error(
        `TBox created in SmartTyre but not found in listing: ${newTrackerCode}`
      );
    }
    const smartTyreTboxId = listResp.records[0].id;
    console.log(`[ProvisionTracker] SmartTyre TBox ID: ${smartTyreTboxId}`);

    // 4. Create ID mapping
    const mappingOk = await createRelationRecord(db, "tbox_id_mapping", {
      quinta_id: localTboxId,
      daijin_id: smartTyreTboxId,
    });
    if (!mappingOk) throw new Error("Failed to create tbox_id_mapping");

    await db.commit();

    event.newTboxId = localTboxId;
    event.newSmartTyreTboxId = smartTyreTboxId;
    event.actionsPerformed = event.actionsPerformed || [];
    event.actionsPerformed.push("tracker_created", "tracker_mapped");

    console.log(
      `[ProvisionTracker] ✅ Complete: local=${localTboxId}, smartyre=${smartTyreTboxId}`
    );
    return event;
  } catch (error) {
    await db.rollback();
    console.error("[ProvisionTracker] Error, rolled back:", error);
    throw error;
  } finally {
    await db.end();
  }
};
