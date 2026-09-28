import { create } from 'zustand';
import type { CrewMember, Station, StationId } from './types';
import { api } from './client';

interface LookupState {
  stations: Station[];
  crew: CrewMember[];
  loaded: boolean;
  load: () => Promise<void>;
  set: (stations: Station[], crew: CrewMember[]) => void;
}

/** Stations and crew change rarely, so they are fetched once after sign-in and read synchronously. */
export const useLookups = create<LookupState>()((set) => ({
  stations: [],
  crew: [],
  loaded: false,
  load: async () => {
    const data = await api<{ stations: Station[]; crew: CrewMember[] }>('/api/lookups');
    set({ stations: data.stations, crew: data.crew, loaded: true });
  },
  set: (stations, crew) => set({ stations, crew, loaded: true }),
}));

export const stations = () => useLookups.getState().stations;
export const crew = () => useLookups.getState().crew;
export const crewById = (id: string | null | undefined) =>
  id ? crew().find((c) => c.id === id) : undefined;
export const stationById = (id: StationId | string | null | undefined) =>
  stations().find((s) => s.id === id);
export const stationName = (id: StationId | string | null | undefined) =>
  stationById(id)?.name ?? 'All stations';
