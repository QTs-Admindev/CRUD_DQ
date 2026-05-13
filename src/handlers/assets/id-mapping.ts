import { getDbConnection } from "../../lib/db";
import { createRelationRecord, IDRelation } from "../../lib/db-operations";
import { SmartTyreService } from "../../lib/smartyre-service";
import { AssetEvent } from "../../lib/event-types";

const listFunctions: Record<string, (params: any) => Promise<any>> = {
  tbox: SmartTyreService.listTboxes,
  sensor: SmartTyreService.listSensors,
  truck: SmartTyreService.listVehicles,
  car: SmartTyreService.listVehicles,
  trailer: SmartTyreService.listVehicles,
  tire: SmartTyreService.listTires,
};

export const handler = async (event: any) => {
  console.log("Received event:", event);
  const { resourceName, matchingField, id } = event;

  if (resourceName == undefined || !matchingField || id == undefined) {
    throw new Error(
      "Missing required parameters: resourceName, matchingField, or id"
    );
  }

  const table = `${resourceName}_id_mapping`;
  const [key, value] = Object.entries(matchingField)[0];

  const db = await getDbConnection();

  try {
    const listFn = listFunctions[resourceName as string];
    if (!listFn) throw new Error(`Unknown resourceName: ${resourceName}`);

    const resourceList = await listFn(matchingField);

    console.log(`Searching for ${resourceName} with ${key} = ${value}`);

    if (resourceList.records.length === 0) {
      throw new Error(`No SmartTyre match for quinta_id: ${value}`);
    }

    const matchedResource = resourceList.records[0];

    const relation: IDRelation = {
      quinta_id: id,
      daijin_id: matchedResource.id,
    };

    const success = await createRelationRecord(db, table, relation);
    if (!success) {
      throw new Error(`Failed to write relation for quinta_id: ${id}`);
    }

    console.log(
      `✅ Relation created for quinta_id ${id} with smartTyreId: ${matchedResource.id}`
    );
    return matchedResource.id;
  } catch (error) {
    console.error(`❌ Error processing quinta_id ${id}:`, error);
    throw error;
  } finally {
    await db.end();
  }
};
