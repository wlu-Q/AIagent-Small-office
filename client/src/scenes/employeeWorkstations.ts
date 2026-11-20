export type EmployeeWorkstation = {
  id: string
  x: number
  y: number
  direction: 'up' | 'down'
}

const workstations: EmployeeWorkstation[] = [
  { id: 'workstation-01', x: -48, y: 320, direction: 'up' },
  { id: 'workstation-02', x: -144, y: 320, direction: 'up' },
  { id: 'workstation-03', x: -240, y: 320, direction: 'up' },
  { id: 'workstation-04', x: -48, y: 544, direction: 'up' },
  { id: 'workstation-05', x: -144, y: 544, direction: 'up' },
  { id: 'workstation-06', x: -240, y: 544, direction: 'up' },
  { id: 'workstation-07', x: 1008, y: 544, direction: 'up' },
  { id: 'workstation-08', x: 1104, y: 544, direction: 'up' },
  { id: 'workstation-09', x: 1200, y: 544, direction: 'up' },
  { id: 'workstation-10', x: 1008, y: 800, direction: 'up' },
  { id: 'workstation-11', x: 1104, y: 800, direction: 'up' },
  { id: 'workstation-12', x: 1200, y: 800, direction: 'up' },
  { id: 'workstation-13', x: -48, y: 224, direction: 'down' },
  { id: 'workstation-14', x: -144, y: 224, direction: 'down' },
  { id: 'workstation-15', x: -240, y: 224, direction: 'down' },
  { id: 'workstation-16', x: -48, y: 448, direction: 'down' },
  { id: 'workstation-17', x: -144, y: 448, direction: 'down' },
  { id: 'workstation-18', x: -240, y: 448, direction: 'down' },
  { id: 'workstation-19', x: 1008, y: 448, direction: 'down' },
  { id: 'workstation-20', x: 1104, y: 448, direction: 'down' },
  { id: 'workstation-21', x: 1200, y: 448, direction: 'down' },
  { id: 'workstation-22', x: 1008, y: 704, direction: 'down' },
  { id: 'workstation-23', x: 1104, y: 704, direction: 'down' },
  { id: 'workstation-24', x: 1200, y: 704, direction: 'down' },
]

const workstationById = new Map(workstations.map((workstation) => [workstation.id, workstation]))

export function getEmployeeWorkstation(workstationId: string) {
  return workstationById.get(workstationId)
}

export function listEmployeeWorkstations() {
  return workstations
}

export function canReachWorkstation(workstation: EmployeeWorkstation, player: { x: number; y: number }) {
  const distanceFromSeat = (player.y - workstation.y) * (workstation.direction === 'up' ? 1 : -1)
  return Math.abs(player.x - workstation.x) <= 56 && distanceFromSeat >= -40 && distanceFromSeat <= 56
}
