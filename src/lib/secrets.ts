import {
  SecretsManagerClient,
  GetSecretValueCommand,
} from "@aws-sdk/client-secrets-manager";

const client = new SecretsManagerClient({ region: "us-west-1" });
const cache: Record<string, string> = {};

/**
 * Retrieves a secret from AWS Secrets Manager with in-memory caching.
 * The cache persists for the lifetime of the Lambda container (across warm invocations).
 */
export async function retrieveSecret(secretName: string): Promise<string> {
  if (cache[secretName]) return cache[secretName];

  const response = await client.send(
    new GetSecretValueCommand({
      SecretId: secretName,
      VersionStage: "AWSCURRENT",
    })
  );

  const value = response.SecretString;
  if (!value) {
    throw new Error(`Secret "${secretName}" has no string value`);
  }

  cache[secretName] = value;
  return value;
}
