export type EmployeeWorkstation = {
  id: string
  x: number
  y: number
  direction: 'up' | 'down' | 'left' | 'right'
}

// These are the six computer-facing chairs on each side of the office. The
// left-side positions come from Game.ts's mirrored workstation objects.
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
]

const workstationById = new Map(workstations.map((workstation) => [workstation.id, workstation]))

export function getEmployeeWorkstation(workstationId: string) {
  return workstationById.get(workstationId)
}

export function listEmployeeWorkstations() {
  return workstations
}
