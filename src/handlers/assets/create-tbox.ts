import { getDbConnection } from "../../lib/db";
import { create } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("input_state:", event);
  const {
    payload: { tboxCode },
    companyId,
  } = event;
  event.stage = "CREATE_VEHICLE";

  if (!tboxCode) throw new Error("tboxCode is required");

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    const record = await create(db, "tboxes", {
      tboxCode,
      company_id: companyId,
      status: "registering",
    });
    const localTboxId = record.id;

    const response = await SmartTyreService.createTbox({ tboxCode });
    if (!response || (response as any) !== "Success") {
      const err = new Error("Failed to create T-Box in SmartTyre");
      err.name = "SmartTyreCreateFailed";
      throw err;
    }

    event.payload.tboxId = localTboxId;
    await db.commit();
    return event;
  } catch (error) {
    await db.rollback();
    throw error;
  } finally {
    await db.end();
  }
};
