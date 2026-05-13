import { getDbConnection } from "../../lib/db";
import { create } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("input_state:", event);

  const {
    payload: { sensorCode },
    companyId,
  } = event;
  event.stage = "CREATE_TIRE";

  if (!sensorCode) throw new Error("sensorCode is required");

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    const record = await create(db, "sensors", {
      sensorCode,
      company_id: companyId,
      version: "404",
      status: "registering",
    });
    const localSensorId = record.id;

    const resp = await SmartTyreService.createSensor({
      sensorCode,
      version: "404",
    });
    if (!resp || (resp as any) !== "Success") {
      throw new Error("Failed to create Sensor in SmartTyre");
    }

    event.payload.sensorId = localSensorId;
    await db.commit();
    return event;
  } catch (error) {
    await db.rollback();
    throw error;
  } finally {
    await db.end();
  }
};
