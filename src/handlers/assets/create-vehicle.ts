import { getDbConnection } from "../../lib/db";
import { create, getById } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { SmartTyreVehicleCreateInput } from "../../lib/smartyre-types";
import { AssetEvent } from "../../lib/event-types";

interface UnitCatalog {
  id: number;
  name: string;
  type: "motive" | "trailer";
  axles_count: number;
  total_tires: number;
  d_id: number;
}

export const handler = async (event: AssetEvent) => {
  console.log("Event received:", event);
  const { companyId, payload } = event;
  const {
    tboxCode,
    tboxId,
    unit_identifier,
    unit_catalog_id,
    status,
    ...vehicleData
  } = payload;

  event.stage = "REGISTER_VEHICLE";

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    const record = await create(db, "trucks", {
      unit_identifier,
      unit_catalog_id,
      tbox_id: tboxId ?? null,
      company_id: companyId,
      ...(vehicleData || {}),
      status: "registering",
    });
    const localVehicleId = record.id;

    const unitTypeData = (await getById(
      db,
      "unit_catalog",
      unit_catalog_id
    )) as UnitCatalog;

    const smartPayload: SmartTyreVehicleCreateInput = {
      isTractor:
        unitTypeData.type === "trailer"
          ? 2
          : unitTypeData.name.includes("truck")
          ? 1
          : 0,
      licensePlateNumber: localVehicleId.toString(),
      axleTypeId: unitTypeData.d_id?.toString(),
      modelId:
        unitTypeData.type === "trailer"
          ? "39"
          : unitTypeData.name.includes("truck")
          ? "40"
          : "32",
      orgId: companyId.toString(),
      ...(tboxCode ? { tboxCode } : {}),
    };

    const resp: any = await SmartTyreService.createVehicle(smartPayload);
    if (!resp || resp !== "Success") {
      throw new Error("Failed to create Vehicle in SmartTyre");
    }

    await db.commit();
    event.payload.id = localVehicleId;
    return event;
  } catch (error) {
    await db.rollback();
    throw error;
  } finally {
    await db.end();
  }
};
