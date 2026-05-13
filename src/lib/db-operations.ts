import { Connection, ResultSetHeader, RowDataPacket } from "mysql2/promise";

// ─────────────────────────────────────────────────────────────────────────────
// Error logging helper
// ─────────────────────────────────────────────────────────────────────────────

function logError(
  operation: string,
  error: any,
  table: string,
  extra?: Record<string, any>
) {
  console.error(
    `[DBOperations] Error in "${operation}" on table "${table}"`,
    { message: error.message, ...(error.sql && { sql: error.sql }), ...extra }
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CREATE — inserts a record and returns the full inserted row
// ─────────────────────────────────────────────────────────────────────────────

export async function create(
  db: Connection,
  table: string,
  data: Record<string, any>
): Promise<RowDataPacket> {
  const fields = Object.entries(data).filter(([, v]) => v !== undefined);
  const columns = fields.map(([k]) => `\`${k}\``).join(", ");
  const placeholders = fields.map(() => "?").join(", ");
  const values = fields.map(([, v]) => v);

  const query = `INSERT INTO \`${table}\` (${columns}) VALUES (${placeholders})`;

  try {
    const [result] = await db.execute<ResultSetHeader>(query, values);
    if (result.affectedRows === 0) throw new Error("No rows affected");

    const [rows] = await db.execute<RowDataPacket[]>(
      `SELECT * FROM \`${table}\` WHERE id = ?`,
      [result.insertId]
    );
    return rows[0];
  } catch (error: any) {
    logError("create", error, table, { query, values });
    throw new Error(`Failed to create record in "${table}"`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// UPDATE — updates by ID and returns the updated row
// ─────────────────────────────────────────────────────────────────────────────

export async function update(
  db: Connection,
  table: string,
  id: number | string,
  data: Record<string, any>
): Promise<RowDataPacket> {
  const fields = Object.entries(data).filter(
    ([key, value]) => key !== "id" && value !== undefined
  );
  if (fields.length === 0) throw new Error("No fields to update");

  const setClause = fields.map(([k]) => `\`${k}\` = ?`).join(", ");
  const values = fields.map(([, v]) => v);
  const query = `UPDATE \`${table}\` SET ${setClause} WHERE \`id\` = ? LIMIT 1`;

  try {
    await db.execute<ResultSetHeader>(query, [...values, id]);

    const [rows] = await db.execute<RowDataPacket[]>(
      `SELECT * FROM \`${table}\` WHERE \`id\` = ?`,
      [id]
    );
    if (rows.length === 0) throw new Error("Record not found after update");
    return rows[0];
  } catch (error: any) {
    logError("update", error, table, { query, id });
    throw new Error(`Failed to update record ${id} in "${table}"`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// UPDATE (generic WHERE) — updates with arbitrary WHERE clause
// ─────────────────────────────────────────────────────────────────────────────

export async function updateWhere(
  db: Connection,
  table: string,
  data: Record<string, any>,
  where: Record<string, any>
): Promise<boolean> {
  const setFields = Object.entries(data).filter(([, v]) => v !== undefined);
  const whereFields = Object.entries(where);

  if (!setFields.length || !whereFields.length) {
    throw new Error("updateWhere requires at least one SET and one WHERE field");
  }

  const setClause = setFields.map(([k]) => `\`${k}\` = ?`).join(", ");
  const whereClause = whereFields.map(([k]) => `\`${k}\` = ?`).join(" AND ");
  const values = [
    ...setFields.map(([, v]) => v),
    ...whereFields.map(([, v]) => v),
  ];

  const query = `UPDATE \`${table}\` SET ${setClause} WHERE ${whereClause}`;

  try {
    const [result] = await db.execute<ResultSetHeader>(query, values);
    return (result.affectedRows ?? 0) > 0;
  } catch (error: any) {
    logError("updateWhere", error, table, { query, values });
    return false;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// DELETE — deletes a single record by ID
// ─────────────────────────────────────────────────────────────────────────────

export async function deleteRecord(
  db: Connection,
  table: string,
  id: number | string
): Promise<boolean> {
  const query = `DELETE FROM \`${table}\` WHERE id = ? LIMIT 1`;

  try {
    const [result] = await db.execute<ResultSetHeader>(query, [id]);
    return result.affectedRows > 0;
  } catch (error: any) {
    logError("deleteRecord", error, table, { id });
    throw new Error(`Failed to delete record ${id} from "${table}"`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// DELETE (generic WHERE) — deletes with arbitrary WHERE clause
// ─────────────────────────────────────────────────────────────────────────────

export async function deleteWhere(
  db: Connection,
  table: string,
  where: Record<string, any>
): Promise<boolean> {
  const whereFields = Object.entries(where);
  if (!whereFields.length) throw new Error("deleteWhere requires at least one WHERE field");

  const whereClause = whereFields.map(([k]) => `\`${k}\` = ?`).join(" AND ");
  const values = whereFields.map(([, v]) => v);
  const query = `DELETE FROM \`${table}\` WHERE ${whereClause}`;

  try {
    const [result] = await db.execute<ResultSetHeader>(query, values);
    return result.affectedRows > 0;
  } catch (error: any) {
    logError("deleteWhere", error, table, { query, values });
    throw new Error(`Failed to delete from "${table}"`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// GET BY ID
// ─────────────────────────────────────────────────────────────────────────────

export async function getById(
  db: Connection,
  table: string,
  id: number | string
): Promise<RowDataPacket | null> {
  const query = `SELECT * FROM \`${table}\` WHERE id = ? LIMIT 1`;

  try {
    const [rows] = await db.execute<RowDataPacket[]>(query, [id]);
    return rows.length ? rows[0] : null;
  } catch (error: any) {
    logError("getById", error, table, { id });
    throw new Error(`Failed to fetch record ${id} from "${table}"`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// FIND SMARTYRE ID — looks up the SmartTyre (daijin) ID from a mapping table
// ─────────────────────────────────────────────────────────────────────────────

export async function findSmartTyreId(
  db: Connection,
  table: string,
  quintaId: number | string
): Promise<string | null> {
  const query = `SELECT daijin_id FROM \`${table}\` WHERE quinta_id = ? LIMIT 1`;

  try {
    const [rows] = await db.execute<RowDataPacket[]>(query, [quintaId]);
    return rows.length > 0 ? (rows[0].daijin_id as string) : null;
  } catch (error: any) {
    logError("findSmartTyreId", error, table, { quintaId });
    throw new Error(`Failed to find SmartTyre ID for quinta_id ${quintaId}`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CREATE RELATION RECORD — inserts into an ID mapping table
// ─────────────────────────────────────────────────────────────────────────────

export interface IDRelation {
  quinta_id: number;
  daijin_id: number;
}

export async function createRelationRecord(
  db: Connection,
  table: string,
  data: IDRelation
): Promise<boolean> {
  const query = `INSERT INTO \`${table}\` (\`quinta_id\`, \`daijin_id\`) VALUES (?, ?)`;

  try {
    const [result] = await db.execute<ResultSetHeader>(query, [
      data.quinta_id,
      data.daijin_id,
    ]);
    return result.affectedRows === 1;
  } catch (error: any) {
    logError("createRelationRecord", error, table, { data });
    return false;
  }
}
