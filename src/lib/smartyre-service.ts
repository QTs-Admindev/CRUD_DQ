import { getSmartTyreClient } from "./smartyre-client";
import {
  SmartTyreVehicleCreateInput,
  SmartTyreVehicle,
  SmartTyreVehicleResponse,
  ListVehiclesParams,
  SmartTyreTireCreateInput,
  SmartTyreTire,
  SmartTyreTireResponse,
  ListTiresParams,
  SmartTyreSensorCreateInput,
  SmartTyreSensorResponse,
  ListSensorsParams,
  SmartTyreTboxCreateInput,
  SmartTyreTboxResponse,
  ListTboxesParams,
  SensorTireBindInput,
  SensorTireUnbindInput,
  TireVehicleBindInput,
  TireVehicleUnbindInput,
} from "./smartyre-types";

/**
 * Unified SmartTyre Service — single source of truth for all SmartTyre proxy API calls.
 * Replaces 8 separate duplicated copies of smartyreService.ts.
 */
export class SmartTyreService {
  // ═══════════════════════════════════════════════════════════════════════════
  // VEHICLES
  // ═══════════════════════════════════════════════════════════════════════════

  static async createVehicle(vehicle: SmartTyreVehicleCreateInput) {
    const api = await getSmartTyreClient();
    return api.post("vehicles", vehicle);
  }

  static async updateVehicle(id: string, vehicle: Partial<SmartTyreVehicle>) {
    const api = await getSmartTyreClient();
    return api.put(`vehicles/${id}`, vehicle);
  }

  static async listVehicles(
    params: ListVehiclesParams = {}
  ): Promise<SmartTyreVehicleResponse> {
    const api = await getSmartTyreClient();
    return api.get("vehicles", { params });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // TIRES
  // ═══════════════════════════════════════════════════════════════════════════

  static async createTire(tire: SmartTyreTireCreateInput) {
    const api = await getSmartTyreClient();
    return api.post("tires", tire);
  }

  static async updateTire(id: string, tire: Partial<SmartTyreTire>) {
    const api = await getSmartTyreClient();
    return api.put(`tires/${id}`, tire);
  }

  static async listTires(
    params: ListTiresParams = {}
  ): Promise<SmartTyreTireResponse> {
    const api = await getSmartTyreClient();
    return api.get("tires", { params });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // SENSORS
  // ═══════════════════════════════════════════════════════════════════════════

  static async createSensor(sensor: SmartTyreSensorCreateInput) {
    const api = await getSmartTyreClient();
    return api.post("sensors", sensor);
  }

  static async updateSensor(
    id: string,
    sensor: Partial<SmartTyreSensorCreateInput>
  ) {
    const api = await getSmartTyreClient();
    return api.put(`sensors/${id}`, sensor);
  }

  static async listSensors(
    params: ListSensorsParams = {}
  ): Promise<SmartTyreSensorResponse> {
    const api = await getSmartTyreClient();
    return api.get("sensors", { params });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // T-BOXES
  // ═══════════════════════════════════════════════════════════════════════════

  static async createTbox(tbox: SmartTyreTboxCreateInput) {
    const api = await getSmartTyreClient();
    return api.post("tboxes", tbox);
  }

  static async updateTbox(
    id: string,
    tbox: Partial<SmartTyreTboxCreateInput>
  ) {
    const api = await getSmartTyreClient();
    return api.put(`tboxes/${id}`, tbox);
  }

  static async listTboxes(
    params: ListTboxesParams = {}
  ): Promise<SmartTyreTboxResponse> {
    const api = await getSmartTyreClient();
    return api.get("tboxes", { params });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // SENSOR ↔ TIRE BINDINGS
  // ═══════════════════════════════════════════════════════════════════════════

  static async bindSensorToTire(binding: SensorTireBindInput) {
    const api = await getSmartTyreClient();
    return api.post("tires/sensor/bind", binding);
  }

  static async unbindSensorFromTire(unbinding: SensorTireUnbindInput) {
    const api = await getSmartTyreClient();
    return api.post("tires/sensor/unbind", unbinding);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // TIRE ↔ VEHICLE BINDINGS
  // ═══════════════════════════════════════════════════════════════════════════

  static async bindTireToVehicle(binding: TireVehicleBindInput) {
    const api = await getSmartTyreClient();
    return api.post("vehicles/tires/bind", binding);
  }

  static async unbindTireFromVehicle(unbinding: TireVehicleUnbindInput) {
    const api = await getSmartTyreClient();
    return api.post("vehicles/tires/unbind", unbinding);
  }
}
