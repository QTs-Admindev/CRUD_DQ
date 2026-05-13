// ═══════════════════════════════════════════════════════════════════════════════
// SmartTyre API Types — Unified from 8 separate copies
// ═══════════════════════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────────────────────────────────────
// Pagination
// ─────────────────────────────────────────────────────────────────────────────

export interface PaginationParams {
  page?: number;
  pageSize?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// VEHICLES
// ─────────────────────────────────────────────────────────────────────────────

export interface SmartTyreVehicleCreateInput {
  isTractor: 0 | 1 | 2;
  licensePlateNumber: string;
  axleTypeId: string;
  modelId: string;
  orgId: string;
  tboxCode?: string;
  tboxId?: string;
}

export interface SmartTyreVehicle {
  id: number;
  isTractor: number;
  licensePlateNumber: string;
  axleTypeId: number | string;
  modelId: number | string;
  orgId: number | string;
  connectedWith: number | null;
  connectedWithNumber: string | null;
  vehicleType: string | null;
  offsetTime: string | null;
  emptyWeight: number | null;
  fullWeight: number | null;
  tboxId: number | null;
  createBy: string | null;
  createAt: string;
  updateBy: string | null;
  updateAt: string;
  isDeleted: boolean | null;
  vehicleAxleName: string | null;
  vehicleAxleCode: string | null;
  vehicleModelName: string | null;
  orgName: string | null;
  forVehicle: any;
  tboxCode: string | null;
  allMatch: boolean;
  tboxStatus: string | null;
  vehicleStatus: number;
  totalTripDistance: number | null;
  periodEndDate: string | null;
  pickupDate: string | null;
  parentVehicleId: string | null;
  vehicleChassisNumber: string | null;
  remark: string | null;
}

export interface SmartTyreVehicleResponse {
  records: SmartTyreVehicle[];
  total: number;
  size: number;
  current: number;
  searchCount: boolean;
  maxLimit: number | null;
  countId: string | null;
  pages: number;
  optimizeCountSql: boolean;
}

export interface ListVehiclesParams extends PaginationParams {
  licensePlateNumber?: string;
  tboxCode?: string;
  orgId?: string;
  vehicleStatus?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// TIRES
// ─────────────────────────────────────────────────────────────────────────────

export interface SmartTyreTireCreateInput {
  tyreCode: string;
  tyreBrandId: string;
  tyreSizeId: string;
  tyrePattern: string;
  initialTreadDepth: string;
  sensorCode?: string;
  totalDistance?: number;
}

export interface SmartTyreTire {
  id: number;
  tyreCode: string;
  tyreBrandId: number;
  tyreBrand: string;
  tyreSizeId: number;
  tyreSize: string;
  orgId: number | null;
  orgName: string | null;
  sensorId: number | null;
  sensorCode: string | null;
  tyrePattern: string;
  tyreDot: string | null;
  origin: string | null;
  wheelDesc: string | null;
  licensePlateNumber: string;
  axleIndex: number;
  wheelIndex: number;
  price: number | null;
  newTreadDepth: number | null;
  initialTreadDepth: number | null;
  nowTreadDepth: number | null;
  loadIndex: string | null;
  speedLevel: string | null;
  initialTreadDepthDate: string | null;
  highTemperatureThreshold: number | null;
  lowPressureThreshold: number | null;
  highPressureThreshold: number | null;
  treadDepthThreshold: number | null;
  createBy: string | null;
  createAt: string;
  updateBy: string | null;
  updateAt: string | null;
  status: number;
  isDeleted: number;
  displayLoad: number;
  totalDistance: number;
  alarmCount: number | null;
  highTemperatureAlarmCount: number | null;
  lowPressureAlarmCount: number | null;
  highPressureAlarmCount: number | null;
  sensorStatus: string | null;
}

export interface SmartTyreTireResponse {
  records: SmartTyreTire[];
  total: number;
  size: number;
  current: number;
  searchCount: boolean;
  maxLimit: number | null;
  countId: string | null;
  pages: number;
  optimizeCountSql: boolean;
  orders: any[];
}

export interface ListTiresParams extends PaginationParams {
  tyreCode?: string;
  tyreBrandId?: number;
  tyreSizeId?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// SENSORS
// ─────────────────────────────────────────────────────────────────────────────

export interface SmartTyreSensorCreateInput {
  sensorCode: string;
  version?: string;
}

export interface SmartTyreSensor {
  id: number;
  sensorCode: string;
  version: string;
  createAt: string;
  status: number;
  updateAt: string | null;
  orgId: number | string;
  orgName?: string | null;
  remark: string;
  isDeleted?: boolean | null;
  createBy?: number | null;
}

export interface SmartTyreSensorResponse {
  records: SmartTyreSensor[];
  total: number;
  size: number;
  current: number;
  pages: number;
}

export interface ListSensorsParams extends PaginationParams {
  sensorCode?: string;
  orgId?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// T-BOXES
// ─────────────────────────────────────────────────────────────────────────────

export interface SmartTyreTboxCreateInput {
  tboxCode: string;
}

export interface SmartTyreTbox {
  id: number;
  tboxCode: string;
  orgId: number | string;
  orgName?: string | null;
  version: string;
  createAt: string;
  status: number;
  updateAt: string | null;
  remark: string;
  isDeleted?: boolean | null;
  createBy?: number | null;
  licensePlateNumber?: string | null;
  carrierId?: number | string | null;
}

export interface SmartTyreTboxResponse {
  records: SmartTyreTbox[];
  total: number;
  size: number;
  current: number;
  pages: number;
}

export interface ListTboxesParams extends PaginationParams {
  tboxCode?: string;
  orgId?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// BINDINGS
// ─────────────────────────────────────────────────────────────────────────────

export interface SensorTireBindInput {
  vehicleId: string;
  tyreCode: string;
  axleIndex: number;
  wheelIndex: number;
  sensorCode: string;
}

export type SensorTireUnbindInput = SensorTireBindInput;

export interface TireVehicleBindInput {
  vehicleId: string;
  tyreCode: string;
  axleIndex: number;
  wheelIndex: number;
}

export interface TireVehicleUnbindInput {
  vehicleId: string;
  tyreCode: string;
}
