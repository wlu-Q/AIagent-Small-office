import type { OfficeEmployeeSummary } from './officeApi'

/** Shared read-through projection, not an authority for employee admission. */
export class EmployeeProjection {
  private revision = 0
  private pending: Promise<OfficeEmployeeSummary[]> | undefined
  private snapshot: OfficeEmployeeSummary[] | undefined
  private expiresAt = 0

  constructor(
    private load: () => Promise<OfficeEmployeeSummary[]>,
    private publish: (employees: OfficeEmployeeSummary[]) => void,
    private now: () => number = Date.now,
  ) {}

  invalidate() {
    this.revision += 1
    this.pending = undefined
    this.expiresAt = 0
  }

  read(fresh = false): Promise<OfficeEmployeeSummary[]> {
    if (this.pending) return this.pending
    if (!fresh && this.snapshot && this.now() < this.expiresAt) return Promise.resolve(this.snapshot)
    const revision = this.revision
    const request = Promise.resolve().then(this.load).then(employees => {
      // A mutation/rebind can finish while an older request is still returning.
      // Its callers must receive the new generation, never publish the old one.
      if (revision !== this.revision) return this.read()
      this.snapshot = employees
      this.expiresAt = this.now() + 1000
      this.publish(employees)
      return employees
    }, error => {
      if (revision !== this.revision) return this.read()
      this.expiresAt = 0
      throw error
    }).finally(() => { if (this.pending === request) this.pending = undefined })
    this.pending = request
    return request
  }
}
