// Origen: LifeOS (nuevo), en el lugar de client/src/net/store.ts de Habblaud. Ver ORIGEN.md.
// Lo que el mundo y la interfaz de Habblaud leen de su fuente de datos. En Habblaud era el store del SSE de su
// servidor; en LifeOS lo implementa `web/src/lifeos-edificio/store.ts` con las rutas existentes de la API. Este
// archivo no conoce LifeOS: solo describe la forma que el código portado espera.
import type { AgentInfo, FeedItem, Notice, OfficeSnapshot, RoomInfo } from '../shared/types';

export type ConnectionState = 'connecting' | 'open' | 'closed' | 'mock';

export interface SourceEvents {
  snapshot: OfficeSnapshot;
  feed: FeedItem[];
  notice: Notice;
  connection: ConnectionState;
}

export interface SnapshotSource {
  readonly snapshot: OfficeSnapshot | null;
  readonly feed: FeedItem[];
  readonly connection: ConnectionState;
  /** Siempre false en LifeOS: no hay datos de demostración. */
  readonly mock: boolean;
  /** Siempre false en LifeOS: no hay timelapse. */
  readonly replaying: boolean;
  /** Próximo reintento tras un fallo (epoch ms), o null. */
  readonly nextRetryAt: number | null;
  on<K extends keyof SourceEvents>(event: K, cb: (value: SourceEvents[K]) => void): () => void;
  agent(id: string): AgentInfo | undefined;
  room(id: string): RoomInfo | undefined;
  agentsInRoom(roomId: string): AgentInfo[];
  reconnectNow(): void;
}
