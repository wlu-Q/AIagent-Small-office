import { t, useLocale, roleLabel } from '../i18n'
import { useEffect, useState } from 'react'
import { Button, Dialog, DialogActions, DialogContent, IconButton, Stack } from '@mui/material'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import styled from '@emotion/styled'
import EmployeeModelSelect from './EmployeeModelSelect'

import {
  officeApi,
  type OfficeEmployeeDetail,
  type OfficeEmployeePersonalInfoInput,
  type OfficeEmployeeSummary,
  type OfficeManagedModel,
  type OfficeEmployeeTools,
} from '../services/office/officeApi'

// Preserve hidden role/extension tools; only the chosen group's members change.
const selectedToolNames = (contract: OfficeEmployeeTools) => Array.from(new Set([
  ...contract.tools, ...contract.choices.filter(choice => choice.required).flatMap(choice => choice.members),
]))

const DialogCloseButton = styled(IconButton)`
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 2;
  border: 1px solid #dce5ee;
  background: rgba(255, 255, 255, 0.92);
  color: #60748a;
  box-shadow: 0 4px 12px rgba(45, 91, 109, 0.08);

  &:hover {
    background: #ffffff;
  }
`

const ManagementLayout = styled.div`
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  flex: 1;
  min-height: 0;

  @media (max-width: 720px) {
    grid-template-columns: 220px minmax(0, 1fr);
  }
`

const EmployeeListPane = styled.aside`
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 18px 12px;
  border-right: 1px solid #dce5ee;
  background: #edf3f7;
`

const ListHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 9px 12px;
  color: #64788e;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.08em;
  text-transform: uppercase;
`

const EmployeeList = styled.div`
  display: grid;
  gap: 7px;
`

const EmployeeItem = styled.button<{ $active: boolean }>`
  width: 100%;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  padding: 10px;
  border: 1px solid ${(props) => props.$active ? '#9fd0cc' : 'transparent'};
  border-radius: 12px;
  background: ${(props) => props.$active ? '#ffffff' : 'transparent'};
  color: #203850;
  text-align: left;
  cursor: pointer;
  box-shadow: ${(props) => props.$active ? '0 7px 18px rgba(45, 91, 109, 0.1)' : 'none'};
  transition: background 150ms ease, border-color 150ms ease, transform 150ms ease;

  &:hover {
    background: #ffffff;
    transform: translateX(2px);
  }

  &:focus-visible {
    outline: 3px solid rgba(65, 148, 143, 0.28);
    outline-offset: 2px;
  }
`

const Avatar = styled.span`
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border: 1px solid #c4e0dc;
  border-radius: 11px;
  background: linear-gradient(145deg, #dff1ef, #bcdedb);
  color: #287d79;
  font-size: 14px;
  font-weight: 850;
`

const EmployeeCopy = styled.span`
  min-width: 0;
  display: grid;
  gap: 3px;

  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: #203850;
    font-size: 12px;
  }

  small {
    color: #71869b;
    font-size: 10px;
  }
`

const DetailPane = styled.main`
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 48px 32px 34px;
  background: #fbfcfd;
`

const Identity = styled.div`
  display: flex;
  align-items: center;
  gap: 13px;
  padding-bottom: 22px;
  border-bottom: 1px solid #e4ebf0;
`

const LargeAvatar = styled(Avatar)`
  width: 54px;
  height: 54px;
  flex-basis: 54px;
  border-radius: 16px;
  font-size: 20px;
`

const IdentityCopy = styled.div`
  min-width: 0;
  display: grid;
  gap: 5px;

  strong,
  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: #172b42;
    font-size: 21px;
    font-weight: 850;
  }

  span {
    color: #71869b;
    font-size: 12px;
  }
`

const DetailDeleteButton = styled.button`
  flex: 0 0 auto;
  margin-left: auto;
  min-height: 34px;
  padding: 0 11px;
  border: 1px solid #fecaca;
  border-radius: 9px;
  background: #fff7f7;
  color: #b42318;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 750;

  &:hover:not(:disabled) {
    background: #fee2e2;
  }

  &:disabled {
    border-color: #e2e8f0;
    background: #f8fafc;
    color: #94a3b8;
    cursor: not-allowed;
  }
`

const Section = styled.section`
  display: grid;
  gap: 9px;
  padding: 22px 0;
  border-bottom: 1px solid #e8eef2;
`

const SectionTitle = styled.h4`
  margin: 0;
  color: #60748a;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.1em;
  text-transform: uppercase;
`

const Fields = styled.div`
  display: grid;
  gap: 16px;

  label {
    display: grid;
    gap: 7px;
    color: #475569;
    font-size: 12px;
    font-weight: 700;
  }

  input[type='text'] {
    width: 100%;
    box-sizing: border-box;
    padding: 10px 11px;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
    outline: none;
    background: #ffffff;
    color: #243b53;
    font: inherit;
    font-weight: 400;

    &:focus {
      border-color: #4c9b98;
      box-shadow: 0 0 0 3px rgba(76, 155, 152, 0.14);
    }

    &:disabled {
      color: #8191a3;
      background: #f8fafc;
    }
  }

  textarea {
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    resize: vertical;
    padding: 12px;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    outline: none;
    background: #ffffff;
    color: #243b53;
    font: inherit;
    font-size: 13px;
    line-height: 1.7;

    &:focus {
      border-color: #4c9b98;
      box-shadow: 0 0 0 3px rgba(76, 155, 152, 0.14);
    }

    &:disabled {
      color: #8191a3;
      background: #f8fafc;
    }
  }

  fieldset {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 12px;
    margin: 0;
    padding: 12px;
    border: 1px solid #dbe4ee;
    border-radius: 10px;
  }

  fieldset label {
    display: flex;
    align-items: center;
    gap: 5px;
    color: #52657f;
    font-size: 12px;
    font-weight: 400;
  }

  p {
    margin: 0;
    color: #687b90;
    font-size: 12px;
    line-height: 1.6;
  }

  [role='alert'] {
    color: #b42318;
  }
`

const ReadonlyBox = styled.div`
  min-height: 42px;
  padding: 11px 12px;
  border: 1px solid #e1e8ed;
  border-radius: 10px;
  background: #ffffff;
  color: #52657f;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
`

const EmptyDetail = styled.div`
  display: grid;
  min-height: 420px;
  place-items: center;
  color: #94a3b8;
  font-size: 13px;
`

type PersonalInfo = Required<OfficeEmployeePersonalInfoInput>
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error)
const listValue = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

export default function OfficeEmployeeEditor({ open, onClose }: { open: boolean; onClose: () => void }) {
  useLocale()
  const [employees, setEmployees] = useState<OfficeEmployeeSummary[]>([])
  const [employeeId, setEmployeeId] = useState('')
  const [detail, setDetail] = useState<OfficeEmployeeDetail | null>(null)
  const [form, setForm] = useState<PersonalInfo | null>(null)
  const [availableSkills, setAvailableSkills] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [stateKnown, setStateKnown] = useState(false)
  const [reload, setReload] = useState(0)
  const [models, setModels] = useState<OfficeManagedModel[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [modelsError, setModelsError] = useState('')
  const [modelReload, setModelReload] = useState(0)
  const [toolContract, setToolContract] = useState<import('../services/office/officeApi').OfficeEmployeeTools | null>(null)
  const [selectedTools, setSelectedTools] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    let active = true
    setModelsLoading(true)
    setModelsError('')
    void officeApi.listManagedModels().then(result => { if (active) setModels(result.models) })
      .catch(() => { if (active) setModelsError(t('ui.unable_to_load_model_list')) })
      .finally(() => { if (active) setModelsLoading(false) })
    return () => { active = false }
  }, [open, modelReload])

  useEffect(() => {
    if (!open) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setStateKnown(false)
    setStatus('')
    const refresh = async () => {
      try {
        const [next, skills] = await Promise.all([officeApi.listEmployees(), officeApi.listSkills()])
        if (!active) return
        setEmployees(next)
        setAvailableSkills(skills.filter((skill) => skill.enabled).map((skill) => skill.name))
        setStateKnown(true)
        setEmployeeId((id) => next.some((employee) => employee.id === id) ? id : next[0]?.id || '')
      } catch (loadError) {
        if (active) { setError(errorText(loadError)); setStateKnown(false) }
      } finally {
        if (active) timer = setTimeout(refresh, 1500)
      }
    }
    void refresh()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [open, reload])

  useEffect(() => {
    setDetail(null)
    setToolContract(null)
    setForm(null)
    setError('')
    setConfirmDelete(false)
    if (!open || !employeeId) return
    let active = true
    void officeApi.getEmployeeTools(employeeId).then(next => {
      if (active) { setToolContract(next); setSelectedTools(selectedToolNames(next)) }
    }).catch(loadError => { if (active) setError(errorText(loadError)) })
    void officeApi.getEmployee(employeeId).then((next) => {
      if (!active) return
      setDetail(next)
      setForm(next.personalInfo)
    }).catch((loadError) => { if (active) setError(errorText(loadError)) })
    return () => { active = false }
  }, [open, employeeId, reload])

  const employee = employees.find((item) => item.id === employeeId)
  const editable = stateKnown && employee?.state === 'idle' && employee.enabled && !saving && !deleting
  const canDelete = stateKnown && !!employee && !employee.isPm && !saving && !deleting

  const change = (patch: Partial<PersonalInfo>) => {
    setForm((current) => current ? { ...current, ...patch } : current)
    setStatus('')
  }

  const refreshEmployees = async () => {
    const next = await officeApi.listEmployees({ fresh: true })
    setEmployees(next)
    return next
  }

  const save = async () => {
    if (!form || !editable || !toolContract) return
    setSaving(true)
    setError('')
    setStatus('')
    let toolsSaved = false
    try {
      if ([...selectedTools].sort().join('\n') !== [...toolContract.tools].sort().join('\n')) {
        const nextTools = await officeApi.saveEmployeeTools(employeeId, selectedTools, toolContract.revision)
        setToolContract(nextTools); setSelectedTools(selectedToolNames(nextTools)); toolsSaved = true
      }
      const skills = (await officeApi.listSkills()).filter((skill) => skill.enabled).map((skill) => skill.name)
      await officeApi.saveEmployeePersonalInfo(employeeId, { ...form, displayName: form.displayName.trim(),
        skills: form.skills.filter((skill) => skills.includes(skill)) })
      try {
        const next = await officeApi.getEmployee(employeeId)
        setDetail(next)
        setForm(next.personalInfo)
        const nextTools = await officeApi.getEmployeeTools(employeeId)
        setToolContract(nextTools); setSelectedTools(selectedToolNames(nextTools))
        await refreshEmployees()
      } catch (refreshError) {
        setError(t('ui.saved_but_details_could_not_refresh_value', { value1: (errorText(refreshError)) }))
      }
      setStatus(t('ui.saved'))
    } catch (saveError) {
      setError(toolsSaved ? t('employee.partial_save_error', { error: errorText(saveError) }) : errorText(saveError))
    } finally {
      setSaving(false)
    }
  }

  const deleteEmployee = async () => {
    if (!employee || !canDelete) return
    setDeleting(true)
    setError('')
    setStatus('')
    try {
      await officeApi.deleteEmployee(employee.id)
      const next = await refreshEmployees()
      setConfirmDelete(false)
      setDetail(null)
      setForm(null)
      setStatus(t('ui.employee_deleted'))
      setEmployeeId(next[0]?.id || '')
    } catch (deleteError) {
      setError(errorText(deleteError))
      setConfirmDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => { if (!saving && !deleting) onClose() }}
      fullWidth
      maxWidth="lg"
      sx={{ zIndex: 23000 }}
      PaperProps={{ sx: { position: 'relative', height: 'min(960px, calc(100dvh - 64px))', overflow: 'hidden', borderRadius: 3, background: '#fbfcfd', color: '#172b42' } }}
      aria-label={t('ui.manage_employees')}
    >
      <DialogCloseButton aria-label={t('ui.close_employee_management')} disabled={saving || deleting} onClick={onClose} size="small"><CloseRoundedIcon /></DialogCloseButton>
      <DialogContent sx={{ p: 0, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {!employees.length && stateKnown ? <EmptyDetail>{t('ui.no_employees_yet')}</EmptyDetail> : (
          <ManagementLayout>
            <EmployeeListPane>
              <ListHeader><span>{t('ui.employees')}</span><span>{t('employees.count', { count: employees.length })}</span></ListHeader>
              <EmployeeList>
                {employees.map((item) => (
                  <EmployeeItem
                    type="button"
                    key={item.id}
                    data-edit-employee-item={item.id}
                    $active={item.id === employeeId}
                    disabled={saving || deleting}
                    onClick={() => setEmployeeId(item.id)}
                  >
                    <Avatar>{item.displayName.slice(0, 1)}</Avatar>
                    <EmployeeCopy><strong>{item.displayName}</strong><small>{roleLabel(item)}</small></EmployeeCopy>
                  </EmployeeItem>
                ))}
              </EmployeeList>
            </EmployeeListPane>
            <DetailPane>
              {detail && employee ? <Fields data-edit-employee-details>
                <Identity>
                  <LargeAvatar>{employee.displayName.slice(0, 1)}</LargeAvatar>
                  <IdentityCopy>
                    <strong data-edit-employee-name-heading>{employee.displayName}</strong>
                    <span data-edit-employee-role>{roleLabel(employee)}</span>
                  </IdentityCopy>
                  <DetailDeleteButton data-delete-employee disabled={!canDelete} onClick={() => setConfirmDelete(true)}>
                    {deleting ? t('ui.deleting') : employee.isPm ? t('ui.built_in_pm') : t('ui.delete_employee')}
                  </DetailDeleteButton>
                </Identity>

                <Section>
                  <SectionTitle>Prompt</SectionTitle>
                  <label>{t('ui.employee_name')}<input type="text" data-edit-employee-name value={form?.displayName || ''} disabled={!editable} onChange={(event) => change({ displayName: event.target.value })} />
                  </label>
                  <label>Prompt
                    <textarea data-edit-employee-prompt value={form?.prompt || ''} disabled={!editable} onChange={(event) => change({ prompt: event.target.value })} />
                  </label>
                  <EmployeeModelSelect value={form?.modelProfileId || ''} models={models}
                    disabled={!editable} loading={modelsLoading} error={modelsError}
                    changed={form?.modelProfileId !== detail.personalInfo.modelProfileId}
                    onChange={modelProfileId => change({ modelProfileId })} onRetry={() => setModelReload(value => value + 1)} />
                </Section>

                <Section>
                  <SectionTitle>Skills</SectionTitle>
                  <fieldset data-edit-employee-skills disabled={!editable}>
                    <legend>{t('ui.personal_skills')}</legend>
                    {availableSkills.map((skill) => <label key={skill}>
                      <input type="checkbox" data-edit-skill={skill} checked={form?.skills.includes(skill) || false} onChange={(event) => change({ skills: event.target.checked ? [...(form?.skills || []), skill] : (form?.skills || []).filter((name) => name !== skill) })} />{skill}
                    </label>)}
                    {!availableSkills.length && !form?.skills.length && <p>{t('ui.no_skills_available')}</p>}
                  </fieldset>
                </Section>

                <Section>
                  <SectionTitle>Tools</SectionTitle>
                  <ReadonlyBox data-edit-employee-tools>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 20px' }}>
                      {toolContract?.choices.map(tool => {
                        const disabled = employee.canDelegate && tool.id === 'powershell_terminal'
                        return <label key={tool.id} title={disabled ? t('ui.pm_can_inspect_terminals_but_cannot_run_or_stop_commands') : tool.description}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#52657f', cursor: tool.required || disabled || !editable ? 'default' : 'pointer' }}>
                        <input type="checkbox" data-edit-tool={tool.id}
                          checked={!disabled && (tool.required || tool.members.every(id => selectedTools.includes(id)))} disabled={!editable || tool.required || disabled}
                          ref={input => { if (input) input.indeterminate = !disabled && !tool.required && tool.members.some(id => selectedTools.includes(id)) && !tool.members.every(id => selectedTools.includes(id)) }}
                          onChange={event => {
                            const checked = event.target.checked
                            setSelectedTools(current => checked ? Array.from(new Set([...current, ...tool.members])) : current.filter(id => !tool.members.includes(id)))
                            setStatus('')
                          }} />
                        {tool.label}{tool.required && <small style={{ color: '#94a3b8' }}>{t('ui.required')}</small>}
                      </label>})}
                    </div>
                    {!toolContract && <span>{t('ui.loading_tools')}</span>}
                  </ReadonlyBox>
                </Section>

                {confirmDelete && <ReadonlyBox>
                  {t('employee.delete_confirmation', { name: employee.displayName, role: roleLabel(employee) })}<Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
                    <Button data-delete-employee-confirm color="error" variant="contained" disabled={deleting} onClick={() => void deleteEmployee()}>{t('ui.confirm_deletion')}</Button>
                    <Button disabled={deleting} onClick={() => setConfirmDelete(false)}>{t('ui.keep_employee')}</Button>
                  </Stack>
                </ReadonlyBox>}
              </Fields> : <EmptyDetail>{t('ui.loading_employee_details')}</EmptyDetail>}
              {error && <p role="alert" data-employee-edit-error>{error}</p>}
              {status && <p role="status" data-employee-edit-status>{status}</p>}
            </DetailPane>
          </ManagementLayout>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 2.5, py: 1.5, borderTop: '1px solid #e4ebf0' }}>
        {!detail && <Button disabled={saving || deleting} onClick={() => setReload((value) => value + 1)}>{t('ui.reload_settings')}</Button>}
        <Button data-close-employee-editor disabled={saving || deleting} onClick={onClose}>{t('ui.close')}</Button>
        <Button data-save-employee variant="contained" disabled={!editable || !toolContract || !form?.displayName.trim() || !form?.prompt.trim()} onClick={() => void save()}>{saving ? t('ui.saving_changes') : t('ui.save_configuration')}</Button>
      </DialogActions>
    </Dialog>
  )
}
