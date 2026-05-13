import { getDbConnection } from "../../lib/db";
import { updateWhere } from "../../lib/db-operations";

export const handler = async (event: any) => {
  console.log("Incoming Step-Function event:", event);

  const db = await getDbConnection();

  const detail = event.detail;
  const executionName = detail.name;
  const eventStatus = detail.status || "RUNNING";

  const rawExecutionState =
    eventStatus === "SUCCEEDED"
      ? JSON.parse(detail.output)
      : typeof detail.input === "string"
      ? JSON.parse(detail.input)
      : detail.input;

  const executionState = Array.isArray(rawExecutionState)
    ? rawExecutionState[0]
    : rawExecutionState;

  const requestId = executionState.jobId;
  const stage = executionState.stage || "UNKNOWN_STAGE";

  console.log("Processing requestId:", requestId, "stage:", stage);

  try {
    const now = Date.now();
    await updateWhere(
      db,
      "asset_creation_reports",
      {
        execution_name: executionName,
        status: eventStatus,
        updated_at: now,
        ...(eventStatus !== "FAILED" ? { stage } : {}),
      },
      { request_id: requestId }
    );
  } catch (err) {
    console.error("Failed to upsert asset_creation_report:", err);
  } finally {
    await db.end();
  }

  return { handled: true };
};
