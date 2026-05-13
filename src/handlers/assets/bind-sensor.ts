import { getDbConnection } from "../../lib/db";
import { findSmartTyreId } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("Bind request:", event);
  event.stage = "BIND_TIRE";

  const { unit_id, id: tyreCode, axleIndex, wheelIndex, sensorCode } =
    event.payload;

  if (unit_id == undefined || !tyreCode) {
    throw new Error("vehicleId and tireCode are required");
  }

  const db = await getDbConnection();

  try {
    const vehicleId = await findSmartTyreId(db, "truck_id_mapping", unit_id);
    if (!vehicleId) {
      throw new Error(
        `No SmartTyre Vehicle match for unit with id ${unit_id}`
      );
    }

    const resp: any = await SmartTyreService.bindSensorToTire({
      vehicleId: parseInt(vehicleId).toString(),
      tyreCode: tyreCode.toString(),
      axleIndex,
      wheelIndex,
      sensorCode,
    });

    const ok = resp === "Success" || resp?.data === "Success";
    if (!ok) throw new Error("Failed to bind Sensor to Tire in SmartTyre");

    console.log(
      `Sensor ${sensorCode} bound to tire ${tyreCode} on vehicle ${vehicleId}`
    );
    event.payload.vehicleId = vehicleId;
    return event;
  } finally {
    await db.end();
  }
};
