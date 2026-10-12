/** Friendly names for schemas generated from the backend's OpenAPI (src/api/schema.ts). */
import type { components } from './schema'

type Schemas = components['schemas']

export type User = Schemas['UserOut']
export type RoleRef = Schemas['RoleRef']
export type LoginRequest = Schemas['LoginRequest']
export type HealthResponse = Schemas['HealthResponse']
export type DashboardRoom = Schemas['DashboardRoom']
export type DashboardRooms = Schemas['DashboardRooms']
export type DashboardSummary = Schemas['DashboardSummary']
export type HousekeepingStatus = Schemas['HousekeepingStatus']
export type OccupancyStatus = Schemas['OccupancyStatus']
export type ReservationStatus = Schemas['ReservationStatus']
export type ReservationDetail = Schemas['ReservationDetail']
export type HotelInfo = Schemas['HotelInfo']
