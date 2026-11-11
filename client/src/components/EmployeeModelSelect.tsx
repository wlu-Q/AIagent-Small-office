import { t, useLocale, providerLabel, thinkingLabel } from '../i18n'
import { MenuItem, Select } from '@mui/material'
import CheckRounded from '@mui/icons-material/CheckRounded'
import KeyboardArrowDownRounded from '@mui/icons-material/KeyboardArrowDownRounded'
import styled from '@emotion/styled'
import type { OfficeManagedModel } from '../services/office/officeApi'

const Wrapper = styled.div`
  display: grid; gap: 8px; margin-top: 4px;
  .heading { display: flex; align-items: center; gap: 12px; }
  .heading label { color: #52657f; font-size: 12px; font-weight: 600; }
  .pending { color: #986922; font-size: 11px; }
  .hint { margin: 0; font-size: 11px; color: #8191a3; line-height: 1.6; }
  .retry { border: 0; background: none; color: #287d7a; padding: 0 4px; cursor: pointer; font: inherit; }
`

type Props = {
  value: string
  models: OfficeManagedModel[]
  disabled: boolean
  loading: boolean
  error: string
  changed: boolean
  onChange: (value: string) => void
  onRetry: () => void
}

export default function EmployeeModelSelect({ value, models, disabled, loading, error, changed, onChange, onRetry }: Props) {
  useLocale()
  const selected = models.find(model => model.id === value)
  const label = (model: OfficeManagedModel) => `${providerLabel(model.provider, model.providerName)} - ${model.modelName} - ${thinkingLabel(model.thinkingLevel)}`
  return <Wrapper>
    <div className="heading"><label id="employee-model-label">{t('ui.model')}</label>{changed && <span className="pending">{t('ui.unsaved')}</span>}</div>
    <Select value={value} disabled={disabled || loading || !!error} displayEmpty labelId="employee-model-label"
      data-edit-employee-model IconComponent={KeyboardArrowDownRounded}
      onChange={event => onChange(event.target.value)}
      renderValue={() => loading ? t('ui.loading_models') : selected ? label(selected) : value ? t('ui.current_model_not_in_the_model_list') : t('ui.select_a_model')}
      sx={{ bgcolor: '#fff', color: '#243b53', borderRadius: '10px', fontSize: 13, minWidth: 0,
        '& .MuiSelect-select': { py: '12px', pr: '40px !important', whiteSpace: 'nowrap', textOverflow: 'ellipsis' },
        '& .MuiOutlinedInput-notchedOutline': { borderColor: '#cbdbe3' },
        '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: '#80b4b1' },
        '&.Mui-focused': { boxShadow: '0 0 0 3px rgba(76,155,152,.12)' },
        '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: '#4c9b98', borderWidth: 1 },
        '& .MuiSelect-icon': { color: '#688f95', right: 12 },
        '&.Mui-disabled': { bgcolor: '#f3f6f8' },
        '& .MuiSelect-select.Mui-disabled': { WebkitTextFillColor: '#8191a3' },
      }}
      MenuProps={{ sx: { zIndex: 24000 }, PaperProps: { sx: { mt: .7, maxHeight: 280, bgcolor: '#fff', color: '#243b53',
        backgroundImage: 'none', border: '1px solid #dbe7ea', borderRadius: '12px', boxShadow: '0 10px 30px rgba(39,68,87,.14)',
        '& .MuiList-root': { p: .6 }, '& .MuiMenuItem-root': { borderRadius: '7px', minHeight: 42, fontSize: 13, gap: 1.5 },
        '& .MuiMenuItem-root:hover': { bgcolor: '#f1f7f7' },
        '& .MuiMenuItem-root.Mui-selected, & .MuiMenuItem-root.Mui-selected:hover, & .MuiMenuItem-root.Mui-selected.Mui-focusVisible': { bgcolor: '#e8f3f2', color: '#256b68' },
        '& .MuiMenuItem-root.Mui-focusVisible': { outline: '2px solid #4c9b98', outlineOffset: -2 },
      } } }}>
      {!value && <MenuItem value="" disabled>{t('ui.select_a_model')}</MenuItem>}
      {value && !selected && <MenuItem value={value} disabled>{t('ui.current_model_not_in_the_model_list')}</MenuItem>}
      {models.map(model => <MenuItem key={model.id} value={model.id} data-employee-model-option={model.id}>
        <span style={{ flex: 1, overflowWrap: 'anywhere', whiteSpace: 'normal' }}>{label(model)}</span>
        {model.id === value && <CheckRounded sx={{ fontSize: 17, flexShrink: 0 }} />}
      </MenuItem>)}
    </Select>
    <p className="hint">{error ? <>{error}<button className="retry" type="button" onClick={onRetry}>{t('ui.retry')}</button></>
      : disabled ? t('ui.change_this_while_the_employee_is_idle_then_save_the_configuration') : t('ui.applies_to_this_employee_changing_the_global_default_does_not_change_this')}</p>
  </Wrapper>
}
