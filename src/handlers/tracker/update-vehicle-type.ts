import { getDbConnection } from "../../lib/db";
import { update, findSmartTyreId } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { TrackerEvent } from "../../lib/event-types";

/**
 * Update Vehicle Type — changes the vehicle type (tractor/trailer/car) in SmartTyre + MySQL.
 * Runs in parallel with the tracker branch.
 */
export const handler = async (event: TrackerEvent): Promise<TrackerEvent> => {
  console.log("[UpdateVehicleType] Input:", JSON.stringify(event));

  const { vehicleId, newVehicleType } = event;

  if (newVehicleType === undefined || newVehicleType === null) {
    throw new Error("newVehicleType is required (0=car, 1=tractor, 2=trailer)");
  }

  const db = await getDbConnection();

  try {
    // 1. Get SmartTyre vehicle ID
    const smartTyreVehicleId = await findSmartTyreId(
      db,
      "truck_id_mapping",
      vehicleId
    );
    if (!smartTyreVehicleId) {
      throw new Error(
        `No SmartTyre mapping for vehicle ${vehicleId}`
      );
    }

    // 2. Update in SmartTyre
    const resp: any = await SmartTyreService.updateVehicle(smartTyreVehicleId, {
      isTractor: newVehicleType,
    } as any);
    console.log("[UpdateVehicleType] SmartTyre response:", resp);

    // 3. Update in MySQL (if you track vehicle type locally)
    // await update(db, "trucks", vehicleId, { vehicle_type: newVehicleType });

    event.actionsPerformed = event.actionsPerformed || [];
    event.actionsPerformed.push("vehicle_type_updated");

    console.log(
      `[UpdateVehicleType] ✅ Vehicle ${vehicleId} type set to ${newVehicleType}`
    );
    return event;
  } finally {
    await db.end();
  }
};
