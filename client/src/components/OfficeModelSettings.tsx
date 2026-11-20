import { t, useLocale, formatDate, providerLabel, thinkingLabel } from '../i18n'
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import Dialog from '@mui/material/Dialog'
import { MenuItem, Select, Switch } from '@mui/material'
import KeyboardArrowDownRounded from '@mui/icons-material/KeyboardArrowDownRounded'
import AddRounded from '@mui/icons-material/AddRounded'
import ArrowBackRounded from '@mui/icons-material/ArrowBackRounded'
import ChevronRightRounded from '@mui/icons-material/ChevronRightRounded'
import CloseRounded from '@mui/icons-material/CloseRounded'
import CheckRounded from '@mui/icons-material/CheckRounded'
import EditOutlined from '@mui/icons-material/EditOutlined'
import VisibilityRounded from '@mui/icons-material/VisibilityRounded'
import VisibilityOffRounded from '@mui/icons-material/VisibilityOffRounded'
import { officeApi, type OfficeModelCatalogue, type OfficeModelProvider, type OfficeModelDetail } from '../services/office/officeApi'
import { pushToast } from '../state/toastStore'

const colours: Record<string, string> = { openai: '#23796c', deepseek: '#4c66d9', mimo: '#b45b23', kimi: '#3c6285', glm: '#6952a6', doubao: '#277d93', minimax: '#b84369', qwen: '#6552be' }
const marks: Record<string, string> = { openai: 'O', deepseek: 'D', mimo: 'Mi', kimi: 'K', glm: 'G', get doubao() { return t('ui.d') }, minimax: 'M', qwen: 'Q' }
const TableFrame = styled.div`
  height: 242px; overflow: auto; border: 1px solid #e0ebee; border-radius: 12px;
  scrollbar-width: thin; scrollbar-color: #c2d2da transparent;
  table { width: 100%; border-collapse: separate; border-spacing: 0; text-align: left; font-size: 13px; }
  th { position: sticky; top: 0; z-index: 1; padding: 12px 14px; color: #61758a; background: #f3f7f9; font-weight: 600; white-space: nowrap; border-bottom: 1px solid #e0ebee; }
  td { padding: 13px 14px; border-bottom: 1px solid #edf2f5; vertical-align: middle; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr[data-default='true'] { background: #f2f8ff; }
  .thinking { white-space: nowrap; color: #405b74; font-weight: 500; }
  .model-name { overflow-wrap: anywhere; min-width: 100px; font-weight: 600; color: #253b51; }
  .date { font-size: 12px; white-space: nowrap; color: #718294; }
  .empty { text-align: center; height: 174px; color: #718294; }
  .actions { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
  .edit-model, .edit-model:disabled { opacity: 0; pointer-events: none; }
  &[data-dialog-open='false'] tbody tr:hover .edit-model,
  &[data-dialog-open='false'] .edit-model:focus-visible { opacity: 1; pointer-events: auto; }
  @media (hover: none) {
    &[data-dialog-open='false'] .edit-model,
    &[data-dialog-open='false'] .edit-model:disabled { opacity: 1; pointer-events: auto; }
  }
  &:focus-visible { outline: 2px solid #2463eb; outline-offset: 3px; }
`
const Identity = styled.span`
  display: inline-flex; align-items: center; gap: 9px; white-space: nowrap;
`
const Mark = styled.span<{ $provider: string }>`
  display: inline-grid; place-items: center; width: 29px; height: 29px; flex-shrink: 0;
  border-radius: 8px; background: #fff; border: 1px solid #e2eaf0;
  font-size: 13px; font-weight: 800; color: ${({ $provider }) => colours[$provider] || '#61758a'};
`
const Button = styled.button<{ $primary?: boolean }>`
  display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 36px;
  padding: 8px 13px; border: 1px solid ${({ $primary }) => $primary ? '#2463eb' : '#d5e1e9'};
  border-radius: 9px; background: ${({ $primary }) => $primary ? '#2463eb' : '#fff'};
  color: ${({ $primary }) => $primary ? '#fff' : '#3c5872'}; font: inherit; font-size: 12px; font-weight: 600;
  cursor: pointer; white-space: nowrap;
  &:hover:not(:disabled) { background: ${({ $primary }) => $primary ? '#1d53cb' : '#f0f6fc'}; }
  &:disabled { cursor: default; opacity: .6; }
  &:focus-visible { outline: 2px solid #2463eb; outline-offset: 3px; }
`
const DefaultButton = styled(Button)<{ $current: boolean }>`
  border-color: transparent;
  background: ${({ $current }) => $current ? '#eaf5f0' : 'transparent'};
  color: ${({ $current }) => $current ? '#397b70' : '#2463eb'};
  &:disabled { opacity: 1; }
`
const ListFooter = styled.div`
  display: flex; justify-content: space-between; align-items: center; gap: 12px; padding-top: 14px;
  p { margin: 0; font-size: 12px; color: #718294; line-height: 1.6; }
`
const EditButton = styled(Button)`
  padding: 8px; border-color: transparent; background: transparent; color: #55758a;
  &:hover:not(:disabled) { color: #23796c; background: #eaf5f0; }
`
const ModalHeader = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 23px 26px 19px; border-bottom: 1px solid #e5edf2;
  h3 { margin: 0; font-size: 21px; font-weight: 700; letter-spacing: -.3px; }
  p { margin: 6px 0 0; color: #718294; font-size: 12px; }
`
const IconButton = styled.button`
  display: grid; place-items: center; flex-shrink: 0; width: 33px; height: 33px; border: 0;
  border-radius: 8px; background: transparent; color: #6b7e91; cursor: pointer;
  &:hover { background: #edf4f8; color: #2463eb; }
  &:focus-visible { outline: 2px solid #2463eb; }
  &:disabled { opacity: .4; cursor: default; }
`
const ProviderGrid = styled.div`
  padding: 24px 26px 28px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
  @media (max-width: 440px) { grid-template-columns: 1fr; padding: 18px; }
`
const ProviderButton = styled.button`
  display: flex; align-items: center; gap: 12px; padding: 17px 15px; border: 1px solid #dfe9ef;
  border-radius: 12px; background: #f9fbfc; color: #253b51; font-size: 14px; font-weight: 600; cursor: pointer;
  &:hover { border-color: #8caeea; background: #f0f6ff; }
  &:focus-visible { outline: 2px solid #2463eb; outline-offset: 2px; }
`
const FormBody = styled.div`
  display: grid; gap: 18px; padding: 22px 26px;
  label, legend { color: #405b74; font-size: 13px; font-weight: 600; }
  label { display: grid; gap: 8px; }
  input:not([type='radio']) { box-sizing: border-box; width: 100%; padding: 12px 13px; border: 1px solid #ccdbe6;
    border-radius: 9px; background: #fff; color: #253b51; font: inherit; font-size: 13px; min-height: 42px; }
  input:focus-visible { outline: 2px solid #a8c5ff; border-color: #2463eb; outline-offset: 1px; }
  input::placeholder { color: #95a4b2; }
  > fieldset { margin: 0; padding: 15px 0 0; border: 0; border-top: 1px solid #e5edf2; }
  > fieldset > legend { padding: 0 0 7px; }
  .radios { display: flex; gap: 26px; }
  .radios label { display: inline-flex; align-items: center; gap: 7px; font-weight: 400; cursor: pointer; }
  input[type='radio'] { accent-color: #2463eb; width: 16px; height: 16px; }
  .secret { position: relative; }
  .secret input { padding-right: 48px; }
  .secret button { position: absolute; right: 6px; top: 5px; }
`
const ModalFooter = styled.div`
  display: flex; align-items: center; gap: 9px; padding: 17px 26px; border-top: 1px solid #e5edf2; background: #f7fafc;
  p { flex: 1; margin: 0; color: #718294; font-size: 11px; line-height: 1.6; }
`
const ThinkingField = styled.div`
  display: grid; gap: 9px; padding-top: 15px; border-top: 1px solid #e5edf2;
  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .switch-copy { display: grid; gap: 5px; }
  .switch-copy span { color: #718294; font-size: 12px; }
`
const errorText = (error: unknown) => (error instanceof Error ? error.message : t('ui.please_try_again_later'))
  .replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '')
const dateText = (value: string | null) => value ? formatDate(value) : t('ui.historical_configuration')

export default function OfficeModelSettings() {
  useLocale()
  const [catalogue, setCatalogue] = useState<OfficeModelCatalogue>({ models: [], providers: [] })
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState<OfficeModelProvider | null>(null)
  const [modelName, setModelName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [vision, setVision] = useState(false)
  const [thinkingLevel, setThinkingLevel] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [editing, setEditing] = useState<OfficeModelDetail | null>(null)
  const [busy, setBusy] = useState('')
  const busyRef = useRef(false)
  const requestId = useRef('')
  const detailRequest = useRef(0)
  const load = async () => {
    setLoading(true)
    try { setCatalogue(await officeApi.listManagedModels()); setLoadFailed(false) }
    catch (error) { setLoadFailed(true); pushToast(t('ui.unable_to_load_models_value', { value1: (errorText(error)) }), 'error') }
    finally { setLoading(false) }
  }
  useEffect(() => {
    void load()
    return () => { detailRequest.current += 1 }
  }, [])
  const start = () => {
    requestId.current = crypto.randomUUID()
    setEditing(null)
    setProvider(null); setModelName(''); setBaseUrl(''); setApiKey(''); setVision(false); setThinkingLevel(''); setShowKey(false); setOpen(true)
  }
  const readingDetail = busy.startsWith('edit:')
  const close = () => {
    if (busyRef.current && !readingDetail) return
    detailRequest.current += 1
    busyRef.current = false; setBusy('')
    setOpen(false); setApiKey(''); setShowKey(false)
  }
  const choose = (next: OfficeModelProvider) => {
    setProvider(next); setBaseUrl(next.baseUrl); setModelName(''); setApiKey(''); setVision(false); setShowKey(false)
    setThinkingLevel(next.defaultThinkingLevel)
  }
  const edit = async (modelId: string) => {
    if (busyRef.current) return
    const summary = catalogue.models.find(item => item.id === modelId)
    if (!summary) return
    const request = ++detailRequest.current
    busyRef.current = true; setBusy(`edit:${modelId}`)
    setEditing({ ...summary, baseUrl: '', apiKeyConfigured: false, editable: false, editReasonCode: '' })
    setProvider(catalogue.providers.find(item => item.id === summary.provider) || { id: '', name: summary.providerName, baseUrl: '', thinkingOptions: [], defaultThinkingLevel: '' })
    setThinkingLevel(summary.thinkingLevel || '')
    setModelName(summary.modelName); setBaseUrl(''); setApiKey(''); setVision(summary.supportsVision); setShowKey(false); setOpen(true)
    try {
      const model = await officeApi.getModel(modelId)
      if (request !== detailRequest.current) return
      setEditing(model)
      setProvider(catalogue.providers.find(item => item.id === model.provider) || { id: '', name: model.providerName, baseUrl: model.baseUrl, thinkingOptions: [], defaultThinkingLevel: '' })
      setModelName(model.modelName); setBaseUrl(model.baseUrl); setVision(model.supportsVision)
      setThinkingLevel(model.thinkingLevel || '')
    } catch (error) {
      if (request !== detailRequest.current) return
      setOpen(false); pushToast(t('ui.unable_to_read_model_value', { value1: (errorText(error)) }), 'error')
    } finally {
      if (request === detailRequest.current) { busyRef.current = false; setBusy('') }
    }
  }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!provider || busyRef.current || (editing && !editing.editable)) return
    const action = editing ? t('ui.save') : t('ui.add')
    if (!modelName.trim() || !baseUrl.trim() || (!apiKey.trim() && !editing?.apiKeyConfigured)) {
      pushToast(t('ui.value_failed_enter_the_model_name_api_url_and_api_key', { value1: (action) }), 'error'); return
    }
    busyRef.current = true; setBusy('save')
    try {
      const payload = { modelName: modelName.trim(), baseUrl: baseUrl.trim(), apiKey: apiKey.trim(), supportsVision: vision,
        ...(provider.thinkingOptions.some(option => option.value === thinkingLevel) ? { thinkingLevel } : {}) }
      setCatalogue(editing ? await officeApi.updateModel(editing.id, payload)
        : await officeApi.addModel({ ...payload, requestId: requestId.current, provider: provider.id }))
      setOpen(false); setApiKey(''); setShowKey(false); pushToast(t('ui.value_succeeded', { value1: (action) }), 'success')
    } catch (error) { pushToast(t('ui.value_failed_value', { value1: (action), value2: (errorText(error)) }), 'error') }
    finally { busyRef.current = false; setBusy('') }
  }
  const setDefault = async (id: string) => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(id)
    try { setCatalogue(await officeApi.setDefaultModel(id)); pushToast(t('ui.set_as_global_default'), 'success') }
    catch (error) { pushToast(t('ui.switch_failed_value', { value1: (errorText(error)) }), 'error') }
    finally { busyRef.current = false; setBusy('') }
  }
  const formDisabled = Boolean(busy) || Boolean(editing && !editing.editable)
  const listDisabled = Boolean(busy) && !open
  return <div data-office-model-settings>
    <TableFrame tabIndex={0} aria-label={t('ui.configured_models')} data-office-model-list data-dialog-open={open}>
      <table><thead><tr><th>{t('ui.provider')}</th><th>{t('ui.model_name')}</th><th>{t('ui.added_2')}</th><th>{t('ui.reasoning')}</th><th>{t('ui.actions')}</th></tr></thead>
        <tbody>{catalogue.models.map(model => <tr key={model.id} data-office-model-row={model.id} data-default={model.isDefault}>
          <td><Identity><Mark $provider={model.provider || ''} aria-hidden="true">{marks[model.provider || ''] || 'M'}</Mark>{providerLabel(model.provider || '', model.providerName)}</Identity></td>
          <td className="model-name">{model.modelName}</td><td className="date">{dateText(model.createdAt)}</td>
          <td className="thinking" data-office-model-thinking={model.id}>{thinkingLabel(model.thinkingLevel)}</td>
          <td><div className="actions"><DefaultButton type="button" $current={model.isDefault} disabled={model.isDefault || listDisabled} data-office-model-default={model.id} onClick={() => void setDefault(model.id)}>
            {model.isDefault && <CheckRounded sx={{ fontSize: 14 }} />}{model.isDefault ? t('ui.current_default') : busy === model.id ? t('ui.switching') : t('ui.global_default')}
          </DefaultButton><EditButton className="edit-model" type="button" disabled={listDisabled}
            aria-label={t('ui.edit_model_value', { value1: (model.modelName) })} data-office-model-edit={model.id} onClick={() => void edit(model.id)}>
            <EditOutlined sx={{ fontSize: 15 }} />{t('ui.edit')}</EditButton></div></td>
        </tr>)}{!catalogue.models.length && <tr><td colSpan={5} className="empty">{loading ? t('ui.loading_models') : loadFailed ? t('ui.model_list_unavailable') : t('ui.no_models_added_yet')}</td></tr>}</tbody>
      </table>
    </TableFrame>
    <ListFooter><p>{t('ui.the_global_default_applies_to_new_employees_existing_employees_keep_their_models')}</p>
      {loadFailed ? <Button onClick={() => void load()} disabled={loading}>{t('ui.reload_settings')}</Button>
        : <Button type="button" onClick={start} disabled={loading || listDisabled} data-office-model-add><AddRounded sx={{ fontSize: 17 }} />{t('ui.add_model')}</Button>}
    </ListFooter>
    <Dialog open={open} onClose={close} onMouseDown={event => event.stopPropagation()} maxWidth={false} data-office-add-model-dialog
      aria-labelledby="office-add-model-title" sx={{ zIndex: 26000 }}
      PaperProps={{ sx: { width: 560, maxWidth: 'calc(100% - 32px)', m: 2, borderRadius: '20px', color: '#172b42', bgcolor: '#fff', backgroundImage: 'none', colorScheme: 'light', boxShadow: '0 24px 80px rgba(23,43,66,.24)' } }}>
      <ModalHeader>
        {provider && !editing && <IconButton aria-label={t('ui.back_to_providers')} disabled={Boolean(busy)} onClick={() => setProvider(null)}><ArrowBackRounded fontSize="small" /></IconButton>}
        <div style={{ flex: 1 }}><h3 id="office-add-model-title">{editing ? t('ui.edit_model') : provider ? t('ui.add_value_model', { value1: (providerLabel(provider.id, provider.name)) }) : t('ui.add_model')}</h3><p>{editing ? t('ui.value_changes_apply_to_all_employees_using_this_model', { value1: (provider ? providerLabel(provider.id, provider.name) : '') }) : provider ? t('ui.enter_the_model_connection_details_add_it_after_verification') : t('ui.choose_a_provider_to_connect_your_next_teammate')}</p></div>
        <IconButton aria-label={editing ? t('ui.close_model_editor') : t('ui.close_add_model')} onClick={close} disabled={Boolean(busy) && !readingDetail}><CloseRounded fontSize="small" /></IconButton>
      </ModalHeader>
      {!provider ? <ProviderGrid>{catalogue.providers.map(item => <ProviderButton key={item.id} data-office-provider={item.id} onClick={() => choose(item)}>
        <Mark $provider={item.id} aria-hidden="true">{marks[item.id]}</Mark><span style={{ flex: 1, textAlign: 'left' }}>{providerLabel(item.id, item.name)}</span><ChevronRightRounded sx={{ fontSize: 18, color: '#8b9bad' }} />
      </ProviderButton>)}</ProviderGrid> : <form onSubmit={save} noValidate aria-busy={readingDetail}>
        <FormBody>
          <label>{t('ui.model_name')}<InputField autoFocus value={modelName} onChange={setModelName} disabled={formDisabled} placeholder={provider.id === 'qwen' ? 'qwen-plus' : t('ui.enter_the_actual_model_id')} name="modelName" /></label>
          <label>{t('ui.api_url')}<InputField value={baseUrl} onChange={setBaseUrl} disabled={formDisabled} placeholder={readingDetail ? '' : provider.baseUrl} name="baseUrl" /></label>
          <label>API Key<div className="secret"><input name="apiKey" autoComplete="off" spellCheck={false} type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)} disabled={formDisabled} placeholder={readingDetail ? '' : editing?.apiKeyConfigured ? t('ui.configured_leave_blank_to_keep') : t('ui.enter_api_key')} />
            <IconButton type="button" disabled={formDisabled} aria-label={showKey ? t('ui.hide_api_key') : t('ui.show_api_key')} onClick={() => setShowKey(!showKey)}>{showKey ? <VisibilityOffRounded fontSize="small" /> : <VisibilityRounded fontSize="small" />}</IconButton></div></label>
          <fieldset><legend>{t('ui.image_input')}</legend><div className="radios"><label><input type="radio" name="vision" checked={vision} onChange={() => setVision(true)} disabled={formDisabled} />{t('ui.supported')}</label><label><input type="radio" name="vision" checked={!vision} onChange={() => setVision(false)} disabled={formDisabled} />{t('ui.not_supported')}</label></div></fieldset>
          {provider.thinkingOptions.length > 0 && <ThinkingField>
            {provider.id === 'mimo' ? <div className="switch-row">
              <div className="switch-copy"><label id="office-thinking-label">{t('ui.enable_reasoning_mode')}</label><span>{thinkingLevel === 'enabled' ? t('ui.enable_reasoning') : thinkingLevel === 'disabled' ? t('ui.disable_reasoning') : t('ui.model_default')}</span></div>
              <Switch checked={thinkingLevel === 'enabled'} disabled={formDisabled}
                onChange={(_, checked) => setThinkingLevel(checked ? 'enabled' : 'disabled')}
                inputProps={{ 'aria-labelledby': 'office-thinking-label', 'data-office-thinking-switch': '' } as React.InputHTMLAttributes<HTMLInputElement>}
                sx={{ '& .MuiSwitch-switchBase.Mui-checked': { color: '#287d7a' }, '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { bgcolor: '#287d7a' },
                  '& .MuiSwitch-switchBase.Mui-disabled': { color: '#9cabb7' },
                  '& .MuiSwitch-switchBase.Mui-disabled + .MuiSwitch-track': { bgcolor: '#b7c5ce', opacity: .6 },
                  '& .MuiSwitch-switchBase.Mui-checked.Mui-disabled': { color: '#85b7b2' },
                  '& .MuiSwitch-switchBase.Mui-checked.Mui-disabled + .MuiSwitch-track': { bgcolor: '#85b7b2', opacity: .5 },
                }} />
            </div> : <>
              <label id="office-thinking-label">{provider.id === 'qwen' ? t('ui.reasoning_mode') : t('ui.reasoning_effort')}</label>
              <Select value={thinkingLevel} disabled={formDisabled} displayEmpty labelId="office-thinking-label"
                data-office-thinking-select IconComponent={KeyboardArrowDownRounded}
                renderValue={() => thinkingLabel(thinkingLevel)}
                onChange={event => setThinkingLevel(event.target.value)}
                sx={{ fontSize: 13, borderRadius: '9px', color: '#253b51', bgcolor: '#fff',
                  '& .MuiSelect-select': { py: '11px' }, '& .MuiOutlinedInput-notchedOutline': { borderColor: '#ccdbe6' },
                  '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: '#80b4b1' },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: '#287d7a', borderWidth: 1 },
                  '&.Mui-focused': { boxShadow: '0 0 0 3px rgba(76,155,152,.12)' }, '& .MuiSelect-icon': { color: '#688f95' },
                  '&.Mui-disabled': { bgcolor: '#f3f6f8' },
                  '& .MuiSelect-select.Mui-disabled': { color: '#61758a', WebkitTextFillColor: '#61758a' },
                  '&.Mui-disabled .MuiOutlinedInput-notchedOutline': { borderColor: '#ccdbe6' },
                  '& .MuiSelect-icon.Mui-disabled': { color: '#8191a3' },
                }}
                MenuProps={{ sx: { zIndex: 27000 }, PaperProps: { sx: { mt: .7, bgcolor: '#fff', color: '#253b51',
                  backgroundImage: 'none', border: '1px solid #dbe7ea', borderRadius: '10px', boxShadow: '0 10px 30px rgba(39,68,87,.14)',
                  '& .MuiList-root': { p: .6 }, '& .MuiMenuItem-root': { minHeight: 38, fontSize: 13, borderRadius: '6px', gap: 2 },
                  '& .MuiMenuItem-root.Mui-selected, & .MuiMenuItem-root.Mui-selected:hover': { bgcolor: '#e8f3f2', color: '#256b68' },
                  '& .MuiMenuItem-root.Mui-focusVisible': { outline: '2px solid #4c9b98', outlineOffset: -2 },
                } } }}>
                {!provider.thinkingOptions.some(option => option.value === thinkingLevel) && <MenuItem value={thinkingLevel} disabled>{thinkingLevel ? (thinkingLabel(thinkingLevel)) : t('ui.model_default')}</MenuItem>}
                {provider.thinkingOptions.map(option => <MenuItem key={option.value} value={option.value} data-office-thinking-option={option.value}>
                  <span style={{ flex: 1 }}>{thinkingLabel(option.value)}</span>{thinkingLevel === option.value && <CheckRounded sx={{ fontSize: 16 }} />}
                </MenuItem>)}
              </Select>
            </>}
          </ThinkingField>}
        </FormBody>
        <ModalFooter><p>{(editing?.editReasonCode ? t(editing.editReasonCode) : '') || (editing ? t('ui.changing_connection_or_reasoning_settings_requires_verification_leave_the_api_key_blank') : t('ui.adding_a_model_verifies_its_connection_and_tool_calling_using_a_small'))}</p><Button type="button" onClick={close} disabled={Boolean(busy) && !readingDetail}>{t('ui.cancel')}</Button><Button type="submit" $primary disabled={formDisabled} data-office-model-submit>{busy === 'save' ? (editing ? t('ui.saving_changes') : t('ui.verifying_and_adding')) : editing ? t('ui.save_changes') : t('ui.add_model')}</Button></ModalFooter>
      </form>}
    </Dialog>
  </div>
}

function InputField({ value, onChange, ...rest }: { value: string; onChange: (value: string) => void; disabled: boolean; placeholder: string; name: string; autoFocus?: boolean }) {
  useLocale()
  return <input {...rest} value={value} onChange={event => onChange(event.target.value)} autoComplete="off" spellCheck={false} />
}
