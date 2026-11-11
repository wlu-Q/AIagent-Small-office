/** Independent, non-overlapping UI reads. A slow lane never blocks another. */
export class RefreshTasks {
  private tasks = new Map<string, { timer?: ReturnType<typeof setTimeout>; failures: number }>()

  constructor(private interval = 700) {}

  ensure<T>(key: string, read: () => Promise<T>, apply: (value: T) => void, failed: (error: unknown) => void) {
    if (this.tasks.has(key)) return
    const task: { timer?: ReturnType<typeof setTimeout>; failures: number } = { failures: 0 }
    this.tasks.set(key, task)
    const current = () => this.tasks.get(key) === task
    const refresh = async () => {
      try {
        const result = await read()
        if (!current()) return
        task.failures = 0
        apply(result)
      } catch (error) {
        if (!current()) return
        task.failures += 1
        failed(error)
      } finally {
        if (current()) task.timer = setTimeout(refresh, Math.min(this.interval * 2 ** Math.min(task.failures, 3), 5000))
      }
    }
    void refresh()
  }

  remove(key: string) {
    const task = this.tasks.get(key)
    if (task?.timer !== undefined) clearTimeout(task.timer)
    this.tasks.delete(key)
  }

  clear() {
    for (const key of this.tasks.keys()) this.remove(key)
  }
}
