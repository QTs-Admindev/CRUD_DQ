import mysql, { Connection } from "mysql2/promise";
import { retrieveSecret } from "./secrets";

/**
 * Creates a new MySQL connection from the secret-stored URI.
 * Each Lambda invocation should create its own connection and close it in `finally`.
 * Do NOT use a singleton here — Lambda concurrency model requires per-invocation connections.
 */
export async function getDbConnection(): Promise<Connection> {
  const uri = await retrieveSecret("MYSQL_AURORA_DB_URI");
  return mysql.createConnection(uri);
}
