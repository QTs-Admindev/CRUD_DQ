import { getDbConnection } from "../../lib/db";
import { create } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { SmartTyreTireCreateInput } from "../../lib/smartyre-types";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("input:", event);
  event.stage = "REGISTER_TIRE";

  const { companyId, resourceName, payload } = event;
  if (resourceName !== "tire") throw new Error("resourceName must be 'tire'");

  const {
    sensorCode,
    sensorId,
    unit_id,
    axleIndex,
    wheelIndex,
    folio,
    prefix,
    tires_catalog_id,
    current_depth,
    unit_type,
    tire_mileage,
    ...tireData
  } = payload;

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    const rec = await create(db, "tires", {
      folio,
      prefix,
      tires_catalog_id,
      unit_id,
      current_depth,
      company_id: companyId,
      sensor_id: sensorId,
      tire_mileage,
      axle_index: axleIndex,
      wheel_index: wheelIndex,
      ...tireData,
      status: "registering",
    });

    const localTireId = rec.id;

    const smartPayload: SmartTyreTireCreateInput = {
      tyreCode: localTireId.toString(),
      tyreBrandId: "1",
      tyreSizeId: "121",
      tyrePattern: "FS591",
      initialTreadDepth: current_depth ? current_depth.toString() : "0",
      totalDistance: tire_mileage ?? 0,
    };

    const resp: any = await SmartTyreService.createTire(smartPayload);
    const ok = resp === "Success" || resp?.data === "Success";
    if (!ok) throw new Error("Failed to create Tire in SmartTyre");

    event.payload.id = localTireId;
    await db.commit();
    return event;
  } catch (error) {
    await db.rollback();
    throw error;
  } finally {
    await db.end();
  }
};
