import axios, { AxiosInstance } from "axios";
import { retrieveSecret } from "./secrets";

let instance: AxiosInstance | null = null;

/**
 * Returns a singleton Axios client configured for the SmartTyre proxy API.
 * The client is created once per Lambda cold start and reused across warm invocations.
 */
export async function getSmartTyreClient(): Promise<AxiosInstance> {
  if (instance) return instance;

  const apiKey = await retrieveSecret("SMARTYRE_API_KEY");
  const baseURL = process.env.SMARTYRE_API_URL!;

  instance = axios.create({
    baseURL,
    headers: {
      "x-api-key": apiKey,
      "Content-Type": "application/json",
    },
    timeout: 15_000,
  });

  // Request logging
  instance.interceptors.request.use((config) => {
    console.log(
      `[SmartTyre] ${config.method?.toUpperCase()} ${config.baseURL}${config.url}`,
      config.data ? JSON.stringify(config.data) : ""
    );
    return config;
  });

  // Response: unwrap `.data` automatically, log errors
  instance.interceptors.response.use(
    (res) => res.data,
    (err) => {
      const detail = err?.response?.data?.detail || err.message;
      console.error(`[SmartTyre] Error: ${detail}`);
      throw new Error(`SmartTyre API error: ${detail}`);
    }
  );

  return instance;
}

/**
 * Resets the client (useful for testing).
 */
export function resetSmartTyreClient(): void {
  instance = null;
}
