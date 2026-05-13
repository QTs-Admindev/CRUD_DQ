import { getDbConnection } from "../../lib/db";
import { update } from "../../lib/db-operations";
import { AssetEvent } from "../../lib/event-types";

export const handler = async (event: AssetEvent) => {
  console.log("input:", event);

  const { resourceName, payload } = event;
  const { id, tboxId, sensorId, status } = payload ?? {};

  if (!resourceName) throw new Error("resourceName is required");
  if (!id) throw new Error("payload.id is required");

  const db = await getDbConnection();
  await db.beginTransaction();

  try {
    switch (resourceName) {
      case "vehicle": {
        await update(db, "trucks", id, { status: "active" });
        if (tboxId !== undefined) {
          await update(db, "tboxes", tboxId, { status: "active" });
        }
        break;
      }
      case "tire": {
        await update(db, "tires", id, { status });
        if (sensorId !== undefined) {
          await update(db, "sensors", sensorId, { status: "active" });
        }
        break;
      }
      default:
        throw new Error(`Unsupported resourceName: ${resourceName}`);
    }

    await db.commit();
    return event;
  } catch (error) {
    await db.rollback();
    throw error;
  } finally {
    await db.end();
  }
};
