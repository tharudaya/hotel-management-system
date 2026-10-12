import { describe, expect, it } from 'vitest'

import { ROOMS } from '../test/fixtures'
import {
  applyFilters,
  filtersFromParams,
  filtersToParams,
  groupByFloor,
  hasActiveFilters,
  NO_FILTERS,
  type QuickView,
  type RoomFilters,
} from './filters'

function numbers(filters: Partial<RoomFilters>): string[] {
  return applyFilters(ROOMS, { ...NO_FILTERS, ...filters }).map((r) => r.number)
}

describe('applyFilters', () => {
  it('returns everything without filters', () => {
    expect(numbers({})).toEqual(['101', '102', '103', '201', '202', '203'])
  })

  it.each<[Partial<RoomFilters>, string[]]>([
    [{ floor: 2 }, ['201', '202', '203']],
    [{ roomType: 'DLX' }, ['103', '202']],
    [{ occupancy: 'arriving_today' }, ['103', '203']],
    [{ housekeeping: 'dirty' }, ['102', '201', '203']],
    [{ search: '20' }, ['201', '202', '203']],
    [{ search: 'lovelace' }, ['102']],
    [{ search: '  TURING ' }, ['201']],
    [{ search: 'nobody' }, []],
  ])('%j', (filters, expected) => {
    expect(numbers(filters)).toEqual(expected)
  })

  it.each<[QuickView, string[]]>([
    ['arrivals', ['103', '201', '203']],
    ['departures', ['201']],
    ['in_house', ['102', '201']],
    ['available', ['101']],
    ['dirty', ['102', '201', '203']],
    ['out_of_order', ['202']],
  ])('view %s', (view, expected) => {
    expect(numbers({ view })).toEqual(expected)
  })

  it('combines filters with AND', () => {
    expect(numbers({ floor: 2, housekeeping: 'dirty' })).toEqual(['201', '203'])
    expect(numbers({ floor: 2, view: 'arrivals', search: 'johnson' })).toEqual(['203'])
    expect(numbers({ floor: 1, roomType: 'DLX', occupancy: 'vacant' })).toEqual([])
  })
})

describe('URL params', () => {
  it('round-trips all filters and keeps unrelated params', () => {
    const filters: RoomFilters = {
      floor: 2,
      roomType: 'DLX',
      occupancy: 'occupied',
      housekeeping: 'clean',
      search: 'ada',
      view: 'in_house',
    }

    const params = filtersToParams(filters, new URLSearchParams('room=4'))

    expect(params.toString()).toBe(
      'room=4&floor=2&type=DLX&occupancy=occupied&hk=clean&q=ada&view=in_house',
    )
    expect(filtersFromParams(params)).toEqual(filters)
  })

  it('removes cleared filters', () => {
    const params = filtersToParams(NO_FILTERS, new URLSearchParams('floor=2&q=x&room=4'))

    expect(params.toString()).toBe('room=4')
  })

  it('ignores invalid values', () => {
    expect(
      filtersFromParams(new URLSearchParams('floor=abc&occupancy=sleeping&hk=&view=all&type=')),
    ).toEqual(NO_FILTERS)
  })

  it('knows when any filter is active', () => {
    expect(hasActiveFilters(NO_FILTERS)).toBe(false)
    expect(hasActiveFilters({ ...NO_FILTERS, search: '   ' })).toBe(false)
    expect(hasActiveFilters({ ...NO_FILTERS, view: 'dirty' })).toBe(true)
  })
})

describe('groupByFloor', () => {
  it('groups rooms by floor in ascending order', () => {
    const shuffled = [...ROOMS].reverse()

    const groups = groupByFloor(shuffled).map((g) => [g.floor, g.rooms.map((r) => r.number)])

    expect(groups).toEqual([
      [1, ['103', '102', '101']],
      [2, ['203', '202', '201']],
    ])
  })
})
