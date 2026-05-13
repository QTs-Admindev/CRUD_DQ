import { SmartTyreService } from "../../lib/smartyre-service";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("Bind request:", event);

  const { id: tyreCode, axleIndex, wheelIndex, vehicleId } = event.payload;

  if (vehicleId == undefined || !tyreCode) {
    throw new Error("vehicleId and tireCode are required");
  }

  const resp: any = await SmartTyreService.bindTireToVehicle({
    vehicleId: parseInt(vehicleId),
    tyreCode: tyreCode.toString(),
    axleIndex,
    wheelIndex,
  });

  const ok = resp === "Success" || resp?.data === "Success";
  if (!ok) throw new Error("Failed to bind Tire to Vehicle in SmartTyre");

  console.log(`Tire ${tyreCode} bound to vehicle ${vehicleId}`);
  return event;
};
